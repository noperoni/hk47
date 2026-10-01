// PERS-35 F7: the only door from the LAN into Archon on the desktop.
//
// Warehouse Traefik used to reach archon-server and vite preview over plain
// HTTP, so anything able to ARP-spoof the LAN could read or rewrite turns and
// gate approvals. Both now bind loopback, and this proxy is the one listener on
// the LAN: TLS 1.3, and the peer must present a client certificate signed by
// the private CA in ~/.archon/tls, which only Traefik holds. Anything else on
// the warehouse fails at the handshake before a byte of HTTP is read.
//
// Routing mirrors the old ingress: /api to the server directly (SSE stays
// unbuffered), everything else to the built bundle. Host is passed through so
// the server's request guard still sees the console hostname.
//
// The CA alone would admit any certificate it ever signed, so the gate also
// pins the one Traefik holds (by SHA-256 of ~/.archon/tls/traefik.crt) and the
// one host it runs on (ARCHON_GATE_PEERS). A second key minted from the CA, or
// Traefik's own key used from anywhere else, is cut off after the handshake. Every
// connection is logged with its peer and fingerprint. Rotating the client cert
// means replacing traefik.crt here and restarting the gate.
import { X509Certificate } from 'node:crypto';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { homedir } from 'node:os';
import { join } from 'node:path';

const dir = process.env.ARCHON_GATE_TLS_DIR ?? join(homedir(), '.archon/tls');
const port = Number(process.env.ARCHON_GATE_PORT ?? '53443');
const apiPort = Number(process.env.ARCHON_API_PORT ?? '53090');
const webPort = Number(process.env.ARCHON_WEB_PORT ?? '55173');
// The proxy host's address, from the untracked ~/.archon/gate.env. No default:
// a gate that guessed its peer would admit the wrong one.
if (!process.env.ARCHON_GATE_PEERS) throw new Error('ARCHON_GATE_PEERS is not set');
const peers = new Set(process.env.ARCHON_GATE_PEERS.split(',').map(s => s.trim()));
const pinned = new X509Certificate(readFileSync(join(dir, 'traefik.crt'))).fingerprint256;

const server = https.createServer(
  {
    key: readFileSync(join(dir, 'server.key')),
    cert: readFileSync(join(dir, 'server.crt')),
    ca: readFileSync(join(dir, 'ca.crt')),
    requestCert: true,
    rejectUnauthorized: true,
    minVersion: 'TLSv1.3',
  },
  (req, res) => {
    const target = /^\/api(?:[/?]|$)/.test(req.url ?? '') ? apiPort : webPort;
    const upstream = http.request(
      { host: '127.0.0.1', port: target, method: req.method, path: req.url, headers: req.headers },
      up => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      }
    );
    upstream.on('error', err => {
      console.error(`upstream :${target} ${req.method} ${req.url}: ${err.message}`);
      if (!res.headersSent) res.writeHead(502).end();
      else res.destroy();
    });
    // A client that goes away mid-stream (closed tab on an SSE feed) must not
    // leave the upstream request open.
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  }
);

server.on('secureConnection', socket => {
  const peer = (socket.remoteAddress ?? '').replace(/^::ffff:/, '');
  const fp = socket.getPeerCertificate().fingerprint256 ?? '';
  const ok = peers.has(peer) && fp === pinned;
  console.log(`${ok ? 'admit' : 'REFUSE'} ${peer} ${fp.slice(0, 23)}`);
  if (!ok) socket.destroy();
});

// SSE responses live for hours; Node's defaults would cut them.
server.requestTimeout = 0;
server.on('tlsClientError', err => console.error(`tls refused: ${err.message}`));
server.listen(port, '0.0.0.0', () => console.log(`archon-gate on :${port}, api :${apiPort}, web :${webPort}`));
