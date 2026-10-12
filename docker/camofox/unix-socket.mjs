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

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (url !== serverUrl || result.source == null) return result;
    const text = Buffer.from(result.source).toString('utf8');
    if (!text.includes(PREWARM_BLOCK)) {
      console.error('[camofox] блок прогрева в server.js не найден: браузер запустится при старте сервера');
      return result;
    }
    return { ...result, source: text.replace(PREWARM_BLOCK, () => PREWARM_SKIPPED) };
  },
});

await import(serverUrl);
