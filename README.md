# FlareSolverr (zendriver + Xvfb Edition)

FlareSolverr is a high-performance proxy server designed to bypass **Cloudflare (Turnstile, 5-second challenges, WAF)** and **DDoS-GUARD** anti-bot protection mechanisms.

This edition replaces the outdated Selenium / undetected-chromedriver stack with **zendriver** running in a **virtual Xvfb desktop** (`:99` 1920x1080), combining direct CDP communication with headful display rendering to defeat modern browser fingerprinting.

---

## Key Improvements

- **Native Cloudflare Turnstile Bypass**: Uses `zendriver`'s built-in `verify_cf` and CDP DOM tree analysis to solve Turnstile checkboxes and interactive challenges in 2–4 seconds.
- **Xvfb Virtual Desktop**: In Linux / Docker environments, Chromium runs headful inside a 1920x1080 Xvfb virtual frame buffer. Cloudflare perceives full GPU canvas rendering, RAF clock intervals, and real screen geometry rather than headless signatures.
- **Concurrency & OOM Protection**: Built-in queue management (`MAX_CONCURRENT_BROWSERS`, default: 3) caps peak memory consumption, queuing concurrent requests from indexers like Jackett, Prowlarr, Sonarr, and Radarr to prevent system OOM crashes.
- **No ChromeDriver Dependency**: Communicates directly with Chromium over Chrome DevTools Protocol (CDP) WebSocket, eliminating driver mismatch errors.

---

## Quick Start (Docker)

Docker is the recommended deployment method as Chromium, Xvfb, fonts, and runtime dependencies are pre-configured.

### Using Docker Run

```bash
docker run -d \
  --name=flaresolverr \
  -p 8191:8191 \
  -e LOG_LEVEL=info \
  -e MAX_CONCURRENT_BROWSERS=3 \
  --restart unless-stopped \
  ghcr.io/zhuxiaole/flaresolverr:latest
```

### Using Docker Compose

```yaml
services:
  flaresolverr:
    image: ghcr.io/zhuxiaole/flaresolverr:latest
    container_name: flaresolverr
    environment:
      - LOG_LEVEL=info
      - MAX_CONCURRENT_BROWSERS=3
      - BROWSER_WAIT_TIMEOUT=1
    ports:
      - "8191:8191"
    restart: unless-stopped
```

---

## Running from Source

1. **Prerequisites**:
   - Python >= 3.10
   - Google Chrome or Chromium installed on the host
   - (Linux only) `xvfb` installed (`apt install -y xvfb` / `apk add xvfb`)

2. **Install & Run**:
   ```bash
   git clone https://github.com/zhuxiaole/FlareSolverr.git
   cd FlareSolverr
   pip install -r requirements.txt
   python src/flaresolverr.py
   ```

---

## API Reference

Endpoint: `POST /v1` (Content-Type: `application/json`)

### 1. `request.get`

Solves challenges on a target URL and returns the rendered HTML and valid cookies (including `cf_clearance`).

```bash
curl -X POST 'http://127.0.0.1:8191/v1' \
  -H 'Content-Type: application/json' \
  --data-raw '{
    "cmd": "request.get",
    "url": "https://example.com/",
    "maxTimeout": 60000
  }'
```

#### Request Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cmd` | `string` | **Required** | Must be `"request.get"` |
| `url` | `string` | **Required** | Target page URL |
| `maxTimeout` | `integer` | `60000` | Max solve timeout in milliseconds |
| `session` | `string` | `null` | Optional persistent session ID |
| `cookies` | `array` | `null` | Pre-existing cookies to inject `[{"name": "...", "value": "..."}]` |
| `returnOnlyCookies`| `boolean` | `false` | When true, skips returning HTML body |
| `returnScreenshot` | `boolean` | `false` | Returns base64 PNG screenshot in `screenshot` field |
| `disableMedia` | `boolean` | `false` | Blocks images, fonts, and stylesheets via CDP to accelerate loading |
| `waitInSeconds` | `integer` | `0` | Delay in seconds after solving before returning response |
| `proxy` | `object` | `null` | `{"url": "http://127.0.0.1:8888", "username": "...", "password": "..."}` |

#### Response Example

```json
{
  "status": "ok",
  "message": "Challenge solved!",
  "startTimestamp": 1729000000000,
  "endTimestamp": 1729000003500,
  "version": "3.5.2",
  "solution": {
    "url": "https://example.com/",
    "status": 200,
    "headers": {},
    "response": "<!DOCTYPE html><html>...</html>",
    "cookies": [
      {
        "name": "cf_clearance",
        "value": "...",
        "domain": ".example.com",
        "path": "/",
        "httpOnly": true,
        "secure": true
      }
    ],
    "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36...",
    "turnstile_token": "0.X.XXXX..."
  }
}
```

---

### 2. `request.post`

Submits POST data through an automated HTML form submit inside the browser.

```bash
curl -X POST 'http://127.0.0.1:8191/v1' \
  -H 'Content-Type: application/json' \
  --data-raw '{
    "cmd": "request.post",
    "url": "https://example.com/login",
    "postData": "username=foo&password=bar",
    "maxTimeout": 60000
  }'
```

---

### 3. Session Management

- **`sessions.create`**: Spawns a dedicated browser instance that retains state and cookies across calls.
- **`sessions.list`**: Returns active session IDs (`{"sessions": ["..."]}`).
- **`sessions.destroy`**: Shuts down the browser instance and releases allocated slots (`{"status": "ok"}`).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MAX_CONCURRENT_BROWSERS` | `3` | Maximum concurrent browser processes before queueing incoming requests |
| `HOST` | `0.0.0.0` | Server bind host address |
| `PORT` | `8191` | Server listening port |
| `LOG_LEVEL` | `info` | Logging verbosity (`debug`, `info`, `warning`, `error`) |
| `LOG_HTML` | `false` | When `true`, dumps page HTML into debug log |
| `HEADLESS` | `true` | Headless mode setting (on Linux, Xvfb automatically enables headful rendering) |
| `DISABLE_MEDIA` | `false` | Globally block images, CSS, and fonts to save memory and bandwidth |
| `BROWSER_WAIT_TIMEOUT` | `1` | Polling check step interval in seconds |
| `BROWSER_PATH` | auto | Explicit path to Chrome / Chromium binary |
| `PROXY_URL` | none | Global upstream proxy URL (`http://host:port` or `socks5://host:port`) |

---

## License

GPL-3.0 License
