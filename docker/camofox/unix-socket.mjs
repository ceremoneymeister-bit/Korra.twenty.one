// Запуск сервера Camofox на Unix-сокете вместо TCP-порта (K21-336).
//
// Контейнеры Korra работают с --network host: любой порт на loopback виден всему
// хосту, и чужой процесс мог бы занять адрес установки и получить её ключ.
// Апстрим открывает соединение ровно в одном месте (app.listen в server.js), и
// мы перенаправляем именно его на сокет из CAMOFOX_UNIX_SOCKET до импорта
// сервера. Любой другой TCP listen в этом процессе запрещён.
//
// Прогрев браузера при старте отключён: апстрим сразу запускает Firefox
// (около 0,5 ГиБ) и держит его пять минут, а Camofox теперь работает у всех
// установок. Файлы пакета не правятся: при загрузке server.js в памяти
// подменяется ровно один блок, браузер запускается первым запросом. Если
// блок в новой версии пакета изменился, сервер стартует с прогревом и пишет
// об этом в лог.
//
// Использование: node unix-socket.mjs /путь/к/server.js
import net from 'node:net';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';

const socketPath = process.env.CAMOFOX_UNIX_SOCKET || '';
const serverScript = process.argv[2] || '';

function die(message) {
  console.error(`[camofox] ${message}`);
  process.exit(1);
}

if (!socketPath || !path.isAbsolute(socketPath)) die('CAMOFOX_UNIX_SOCKET должен быть абсолютным путём');
if (!serverScript) die('не указан server.js');

fs.mkdirSync(path.dirname(socketPath), { recursive: true, mode: 0o700 });
try {
  fs.unlinkSync(socketPath);
} catch (err) {
  if (err.code !== 'ENOENT') die(`не удалось убрать старый сокет: ${err.message}`);
}

const originalListen = net.Server.prototype.listen;
let redirected = false;

function isUnixListen(args) {
  const first = args[0];
  if (typeof first === 'string' && Number.isNaN(Number(first))) return true;
  return Boolean(first && typeof first === 'object' && typeof first.path === 'string');
}

net.Server.prototype.listen = function listen(...args) {
  if (isUnixListen(args)) return originalListen.apply(this, args);
  if (redirected) throw new Error('camofox: TCP listen запрещён, сервер работает только на Unix-сокете');
  redirected = true;
  const callback = args.find((arg) => typeof arg === 'function');
  // Сокет создаётся с правами 0600 сразу, без окна, в котором он доступен другим.
  const previousUmask = process.umask(0o177);
  this.once('listening', () => {
    process.umask(previousUmask);
    try {
      fs.chmodSync(socketPath, 0o600);
    } catch (err) {
      console.error(`[camofox] chmod сокета: ${err.message}`);
      process.exit(1);
    }
  });
  this.once('error', () => process.umask(previousUmask));
  return originalListen.call(this, socketPath, callback);
};

const PREWARM_BLOCK =
  "    await ensureBrowser();\n" +
  "    log('info', 'browser pre-warmed', { ms: Date.now() - start });\n" +
  "    scheduleBrowserIdleShutdown();\n";
const PREWARM_SKIPPED = "    log('info', 'browser pre-warm skipped (Korra): the first request starts it');\n";
const serverUrl = pathToFileURL(serverScript).href;

// isConnected() не проверяет способность Firefox создавать контексты после
// уборки вкладок. Проверяем context + page + close с одним сроком, не вызывая
// ensureBrowser (health не должен запускать браузер или сдвигать idle timer).
// Общая pending-проба и защёлка отказа не дают health накапливать зависшие
// фабрики. После отказа Korra перезапускает весь сервис через s6.
const HEALTH_ROUTE = "app.get('/health', (req, res) => {";
const CONTEXT_HEALTH_ROUTE = `
let korraContextProbe = null;
let korraContextFailed = false;
async function korraContextReady() {
  if (typeof korraSessionClosures !== 'undefined') await Promise.all([...korraSessionClosures]);
  if (korraContextFailed) return false;
  if (!browser || !browser.isConnected()) return true;
  if (!korraContextProbe) {
    korraContextProbe = (async () => {
      let timer;
      const work = (async () => {
        const context = await browser.newContext();
        try { await context.newPage(); }
        finally { await context.close(); }
      })();
      try {
        await Promise.race([
          work,
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error('context/page readiness timeout')), 5000);
          }),
        ]);
        return true;
      } catch (err) {
        korraContextFailed = true;
        console.error('[camofox] context/page readiness failed: ' + err.message);
        return false;
      } finally { clearTimeout(timer); }
    })().finally(() => { korraContextProbe = null; });
  }
  return korraContextProbe;
}
app.get('/health', async (req, res) => {
  if (!await korraContextReady()) {
    return res.status(503).json({ ok: false, engine: 'camoufox', contextReady: false });
  }
`;

// Reaper closes the last context but retains the shared Firefox. Do not
// reuse that empty process: persist first, close it through upstream's own
// process-tree cleanup, then let the next request launch lazily. Serialize
// teardown with ensureBrowser so a new profile cannot acquire the dying one.
const SESSION_CLOSE = 'async function closeSession(userId, session, {';
const SESSION_CLEANUP = '  await session.context.close().catch(() => {});\n  sessions.delete(key);';
const BROWSER_ENSURE = 'async function ensureBrowser() {\n  clearBrowserIdleTimer();';
const REAP_PAGE = '            safePageClose(tabState.page);\n            group.delete(tabId);';
const CONTEXT_CREATE = 'const context = await b.newContext(contextOptions);';
const SESSION_LIFECYCLE = `
const korraSessionClosures = new Set();
function korraBounded(work, stage) {
  let timer;
  const expired = new Promise(() => {
    timer = setTimeout(() => {
      console.error('[camofox] ' + stage + ' exceeded 5s; terminating service and browser tree');
      killBrowserChildren(true);
      process.exit(1);
    }, 5000);
  });
  return Promise.race([work, expired]).finally(() => clearTimeout(timer));
}
async function closeSession(userId, session, options) {
  if (!session) return;
  if (session._korraClose) return session._korraClose;
  session._closing = true;
  const work = korraBounded(korraCloseSession(userId, session, options), 'session teardown');
  korraSessionClosures.add(work);
  session._korraClose = work.finally(() => korraSessionClosures.delete(work));
  return session._korraClose;
}
async function korraCloseSession(userId, session, {`;

// Виртуальный экран. camoufox-js запускает Xvfb с -ac -nolisten tcp: файловый сокет
// в /tmp контейнера остаётся, но Xvfb ещё слушает абстрактный сокет
// @/tmp/.X11-unix/X*, а он живёт в сетевом пространстве имён хоста и доступен всем
// контейнерам без авторизации (можно снять экран и вводить события в браузер).
// Аргументы собирает базовый VirtualDisplay.xvfb_args, на который опираются и
// DefaultVirtualDisplay сервера, и плагин vnc, поэтому правится он один. Если
// блок не найден, сервер не запускается: открытый экран хуже недоступного Camofox.
const XVFB_NOLISTEN = /("-nolisten",\s*"tcp",)/;
const XVFB_NOLISTEN_LOCAL = '$1 "-nolisten", "local",';
const virtdisplayPattern = /[\\/]camoufox-js[\\/]dist[\\/]virtdisplay\.js$/;

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (result.source == null) return result;
    if (virtdisplayPattern.test(url)) {
      const text = Buffer.from(result.source).toString('utf8');
      if (!XVFB_NOLISTEN.test(text)) {
        console.error('[camofox] в camoufox-js не найден блок аргументов Xvfb: без -nolisten local экран виден соседним контейнерам, запуск остановлен');
        process.exit(1);
      }
      return { ...result, source: text.replace(XVFB_NOLISTEN, XVFB_NOLISTEN_LOCAL) };
    }
    if (url !== serverUrl) return result;
    let text = Buffer.from(result.source).toString('utf8');
    if (text.includes(HEALTH_ROUTE)) {
      text = text.replace(HEALTH_ROUTE, () => CONTEXT_HEALTH_ROUTE);
    } else if (/[\\/]@askjo[\\/]camofox-browser[\\/]server\.js$/.test(serverScript)) {
      die('блок /health в server.js не найден: проверка context/page не установлена');
    }
    const lifecycle = [SESSION_CLOSE, SESSION_CLEANUP, BROWSER_ENSURE, CONTEXT_CREATE, REAP_PAGE];
    const pinned = /[\\/]@askjo[\\/]camofox-browser[\\/]server\.js$/.test(serverScript);
    if (pinned && lifecycle.some(block => text.split(block).length !== 2)) {
      die('блок жизненного цикла server.js изменился: безопасная выгрузка Firefox не установлена');
    }
    if (text.includes(SESSION_CLOSE)) {
      text = text.replace(SESSION_CLOSE, () => SESSION_LIFECYCLE)
        .replace(SESSION_CLEANUP, () => `  await session.context.close().catch(() => {});
  if (sessions.get(key) === session) sessions.delete(key);
  if (sessions.size === 0) {
    await closeBrowserFully('idle_shutdown');
  }`)
        .replace(BROWSER_ENSURE, () => `async function ensureBrowser() {
  await Promise.all([...korraSessionClosures]);
  clearBrowserIdleTimer();`)
        .replace(REAP_PAGE, () => `            // Keep the final page alive until the persistence hook has read storage.
            if (group.size !== 1 || session.tabGroups.size !== 1 || hasActivePageLeases(session)) {
              safePageClose(tabState.page);
            }
            group.delete(tabId);`)
        .replace(CONTEXT_CREATE, () => "const context = await korraBounded(b.newContext(contextOptions), 'newContext');");
    }
    if (!text.includes(PREWARM_BLOCK)) {
      console.error('[camofox] блок прогрева в server.js не найден: браузер запустится при старте сервера');
      return { ...result, source: text };
    }
    return { ...result, source: text.replace(PREWARM_BLOCK, () => PREWARM_SKIPPED) };
  },
});

// Страховка выгрузки браузера. Апстрим закрывает пустую сессию без таймаута: если
// context.close() или сохранение состояния зависло, сессия остаётся в списке,
// таймер простоя браузера не взводится и Firefox (около 0,5 ГиБ) с Xvfb живёт
// до перезапуска контейнера (наблюдалось на sandbox21 после захода на тяжёлые
// сайты). Обёртка раз в минуту читает /health своего же сервера; если браузер
// запущен, а вкладок нет дольше, чем апстриму нужно на штатную выгрузку
// (таймаут сессии + таймаут простоя + две минуты запаса), она убивает дочерние
// процессы Firefox. Это рвёт зависший вызов, сессия закрывается, следующий
// запрос запускает браузер заново. Если и после этого /health остаётся нездоровым
// (апстрим вошёл в перезапуск и ждёт то же зависшее закрытие сессии), процесс
// завершается целиком вместе с Xvfb, и s6 поднимает сервер заново.
function envMs(name, fallback) {
  const value = parseInt(process.env[name], 10);
  return Number.isNaN(value) ? fallback : value;
}

function stuckLimitMs() {
  const override = envMs('KORRA_CAMOFOX_STUCK_LIMIT_MS', null);
  if (override !== null) return override;
  const sessionMs = envMs('SESSION_TIMEOUT_MS', 600000);
  const idleMs = envMs('BROWSER_IDLE_TIMEOUT_MS', 300000);
  if (sessionMs <= 0 || idleMs <= 0) return 0;
  return sessionMs + idleMs + 120000;
}

function readHealth() {
  return new Promise((resolve) => {
    const request = http.get({ socketPath, path: '/health', timeout: 5000, agent: false }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try { resolve(JSON.parse(body)); } catch { resolve(null); }
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(null));
  });
}

function killBrowserChildren(includeDisplay = false) {
  const parents = new Map();
  const roots = [];
  for (const entry of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const stat = fs.readFileSync(`/proc/${entry}/stat`, 'utf8');
      const comm = stat.slice(stat.indexOf('(') + 1, stat.lastIndexOf(')'));
      const parent = Number(stat.slice(stat.lastIndexOf(')') + 1).trim().split(' ')[1]);
      parents.set(Number(entry), parent);
      if (parent === process.pid && (/^(camoufox|firefox)/.test(comm) || (includeDisplay && comm === 'Xvfb'))) roots.push(Number(entry));
    } catch { /* процесс уже завершился */ }
  }
  // Остановленные или зависшие процессы содержимого не умирают вместе с родителем.
  const victims = new Set(roots);
  for (let grew = true; grew;) {
    grew = false;
    for (const [pid, parent] of parents) {
      if (!victims.has(pid) && victims.has(parent)) {
        victims.add(pid);
        grew = true;
      }
    }
  }
  let killed = 0;
  for (const pid of victims) {
    try {
      process.kill(pid, 'SIGKILL');
      killed += 1;
    } catch { /* уже завершился */ }
  }
  return killed;
}

const stuckLimit = stuckLimitMs();
if (stuckLimit > 0) {
  const tickMs = Math.max(100, Math.min(60000, Math.floor(stuckLimit / 5)));
  const recoveryMs = envMs('KORRA_CAMOFOX_STUCK_RECOVERY_MS', 120000);
  let emptySince = null;
  let killedAt = null;
  setInterval(async () => {
    const health = await readHealth();
    const now = Date.now();
    if (killedAt !== null) {
      if (health && health.ok === true) {
        killedAt = null;
        emptySince = null;
        return;
      }
      // Закрытие сессии зависло не в Firefox: после убийства браузера апстрим входит в
      // перезапуск (/health: recovering) и ждёт то же закрытие. Единственный выход —
      // завершить процесс, s6 поднимет сервер заново (сохранённые входы остаются на диске).
      if (now - killedAt >= recoveryMs) {
        console.error(`[camofox] после принудительного завершения Firefox сервер не восстановился за ${Math.round(recoveryMs / 1000)} с: перезапуск процесса (${killBrowserChildren(true)} процессов)`);
        process.exit(1);
      }
      return;
    }
    if (!health) return;
    if (!health.browserRunning || health.activeTabs > 0) {
      emptySince = null;
      return;
    }
    if (emptySince === null) emptySince = now;
    if (now - emptySince < stuckLimit) return;
    emptySince = null;
    console.error(`[camofox] браузер без вкладок дольше ${Math.round(stuckLimit / 1000)} с и не выгружен: принудительное завершение Firefox (${killBrowserChildren()} процессов)`);
    killedAt = now;
  }, tickMs).unref();
}

await import(serverUrl);
