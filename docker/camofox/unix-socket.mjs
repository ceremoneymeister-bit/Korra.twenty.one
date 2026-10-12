// Запуск сервера Camofox на Unix-сокете вместо TCP-порта (K21-336).
//
// Контейнеры Korra работают с --network host: любой порт на loopback виден всему
// хосту, и чужой процесс мог бы занять адрес установки и получить её ключ.
// Апстрим открывает соединение ровно в одном месте (app.listen в server.js), и
// мы перенаправляем именно его на сокет из CAMOFOX_UNIX_SOCKET до импорта
// сервера. Любой другой TCP listen в этом процессе запрещён.
//
// Использование: node unix-socket.mjs /путь/к/server.js
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
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

await import(pathToFileURL(serverScript).href);
