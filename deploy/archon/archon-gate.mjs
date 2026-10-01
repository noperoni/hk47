// PERS-35 F7 + PERS-36: the only door from the LAN into Archon on the desktop.
//
// Two TLS layers, one inside the other:
//
//   outer: Traefik -> gate. TLS 1.3, and the peer must present a client
//     certificate signed by the private CA in ~/.archon/tls, which only Traefik
//     holds. Anything else on the warehouse fails here before a byte is read.
//   inner: browser -> gate. Traefik routes by SNI and passes the browser's TLS
//     through untouched, so it is terminated here with the public certificate
//     (issued on this desktop by lego, key never leaves it). A hijacked Traefik
//     relays ciphertext it cannot read or rewrite.
//
// The CA alone would admit any certificate it ever signed, so the outer layer
// also pins the one Traefik holds (by SHA-256 of ~/.archon/tls/traefik.crt) and
// the one host it runs on (ARCHON_GATE_PEERS). Rotating the client cert means
// replacing traefik.crt here and restarting the gate.
//
// The inner layer speaks HTTP/2 (HTTP/1.1 as fallback): each console tab holds
// several SSE streams, and HTTP/1.1's six connections per host would stall the
// second tab. Routing mirrors the old ingress: /api to the server directly (SSE
// stays unbuffered), everything else to the built bundle.
//
// Traefik no longer sees headers, so it cannot overwrite them either: every
// X-Archon-* and forwarded header a browser sends is dropped here, before the
// server can be tricked into trusting one.
import { X509Certificate } from 'node:crypto';
import { readFileSync, watchFile } from 'node:fs';
import http from 'node:http';
import http2 from 'node:http2';
import { homedir } from 'node:os';
import { join } from 'node:path';
import tls from 'node:tls';

const need = name => {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set`);
  return v;
};

const dir = process.env.ARCHON_GATE_TLS_DIR ?? join(homedir(), '.archon/tls');
const port = Number(process.env.ARCHON_GATE_PORT ?? '53443');
const apiPort = Number(process.env.ARCHON_API_PORT ?? '53090');
const webPort = Number(process.env.ARCHON_WEB_PORT ?? '55173');
// From the untracked ~/.archon/gate.env, no defaults: a gate that guessed its
// peer would admit the wrong one, and the public cert paths name the host.
const peers = new Set(need('ARCHON_GATE_PEERS').split(',').map(s => s.trim()));
const publicCert = need('ARCHON_GATE_PUBLIC_CERT');
const publicKey = need('ARCHON_GATE_PUBLIC_KEY');
const pinned = new X509Certificate(readFileSync(join(dir, 'traefik.crt'))).fingerprint256;

const HOP = new Set([
  'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding', 'upgrade', 'http2-settings', 'te',
]);
const SPOOFABLE = /^(x-archon-|x-forwarded-|forwarded$|x-real-ip$)/;

function upstreamHeaders(req) {
  const out = {};
  for (const [k, v] of Object.entries(req.headers)) {
    if (k.startsWith(':') || HOP.has(k) || SPOOFABLE.test(k)) continue;
    out[k] = v;
  }
  // The server's request guard checks Host; HTTP/2 carries it as :authority.
  out.host = req.headers[':authority'] ?? req.headers.host;
  return out;
}

function downstreamHeaders(headers) {
  return Object.fromEntries(Object.entries(headers).filter(([k]) => !HOP.has(k)));
}

const inner = http2.createSecureServer(
  {
    key: readFileSync(publicKey),
    cert: readFileSync(publicCert),
    allowHTTP1: true,
    minVersion: 'TLSv1.3',
  },
  (req, res) => {
    const path = req.url ?? '/';
    const target = /^\/api(?:[/?]|$)/.test(path) ? apiPort : webPort;
    const upstream = http.request(
      { host: '127.0.0.1', port: target, method: req.method, path, headers: upstreamHeaders(req) },
      up => {
        res.writeHead(up.statusCode ?? 502, downstreamHeaders(up.headers));
        up.pipe(res);
      }
    );
    upstream.on('error', err => {
      console.error(`upstream :${target} ${req.method} ${path}: ${err.message}`);
      if (!res.headersSent) res.writeHead(502).end();
      else res.destroy();
    });
    // A client that goes away mid-stream (closed tab on an SSE feed) must not
    // leave the upstream request open.
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  }
);
// SSE responses live for hours; no idle timeout on either protocol.
inner.setTimeout(0);
// A renewal rewrites the cert in place; pick it up for new handshakes without
// a restart that would cut every open stream.
watchFile(publicCert, { interval: 60_000 }, () => {
  try {
    inner.setSecureContext({ key: readFileSync(publicKey), cert: readFileSync(publicCert), minVersion: 'TLSv1.3' });
    console.log('public certificate reloaded');
  } catch (err) {
    console.error(`public certificate reload failed, keeping the old one: ${err.message}`);
  }
});
inner.on('tlsClientError', err => console.error(`inner tls refused: ${err.message}`));

const outer = tls.createServer({
  key: readFileSync(join(dir, 'server.key')),
  cert: readFileSync(join(dir, 'server.crt')),
  ca: readFileSync(join(dir, 'ca.crt')),
  requestCert: true,
  rejectUnauthorized: true,
  minVersion: 'TLSv1.3',
});

outer.on('secureConnection', socket => {
  const peer = (socket.remoteAddress ?? '').replace(/^::ffff:/, '');
  const fp = socket.getPeerCertificate().fingerprint256 ?? '';
  const ok = peers.has(peer) && fp === pinned;
  console.log(`${ok ? 'admit' : 'REFUSE'} ${peer} ${fp.slice(0, 23)}`);
  if (!ok) return socket.destroy();
  socket.on('error', err => console.error(`outer ${peer}: ${err.message}`));
  // The decrypted outer stream carries the browser's own TLS: hand it to the
  // inner server as if it were a fresh connection.
  inner.emit('connection', socket);
});

outer.on('tlsClientError', err => console.error(`tls refused: ${err.message}`));
outer.listen(port, '0.0.0.0', () => console.log(`archon-gate on :${port}, api :${apiPort}, web :${webPort}`));
