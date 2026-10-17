import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from typing import Optional, Any

FLARESOLVERR_VERSION = None
PLATFORM_VERSION = None
CHROME_EXE_PATH = None
CHROME_MAJOR_VERSION = None
USER_AGENT = None

MAX_CONCURRENT_BROWSERS = int(os.environ.get('MAX_CONCURRENT_BROWSERS', 3))
browser_semaphore = threading.Semaphore(MAX_CONCURRENT_BROWSERS)

_xvfb_lock = threading.Lock()
_loop = None
_loop_thread = None
_loop_lock = threading.Lock()


def get_config_log_html() -> bool:
    return os.environ.get('LOG_HTML', 'false').lower() == 'true'


def get_config_headless() -> bool:
    return os.environ.get('HEADLESS', 'true').lower() == 'true'


def get_config_disable_media() -> bool:
    return os.environ.get('DISABLE_MEDIA', 'false').lower() == 'true'


def get_config_browser_wait_timeout() -> int:
    return int(os.environ.get('BROWSER_WAIT_TIMEOUT', 1))


def get_flaresolverr_version() -> str:
    global FLARESOLVERR_VERSION
    if FLARESOLVERR_VERSION is not None:
        return FLARESOLVERR_VERSION

    package_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'package.json')
    if not os.path.isfile(package_path):
        package_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'package.json')
    with open(package_path) as f:
        FLARESOLVERR_VERSION = json.loads(f.read())['version']
        return FLARESOLVERR_VERSION


def get_current_platform() -> str:
    global PLATFORM_VERSION
    if PLATFORM_VERSION is not None:
        return PLATFORM_VERSION
    PLATFORM_VERSION = os.name
    return PLATFORM_VERSION


def get_event_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()

            def _loop_exception_handler(loop, context):
                exc = context.get("exception")
                if isinstance(exc, (ValueError, ConnectionResetError)) and "websocket" in str(exc).lower():
                    return
                if isinstance(exc, asyncio.CancelledError):
                    return
                loop.default_exception_handler(context)

            _loop.set_exception_handler(_loop_exception_handler)
            _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True, name="zendriver_loop")
            _loop_thread.start()
        return _loop


def run_async(coro, timeout: Optional[float] = None):
    loop = get_event_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def ensure_xvfb() -> bool:
    """在无 DISPLAY 的 Linux/Docker 环境下自动拉起 Xvfb 虚拟桌面，供真实有头浏览器渲染。"""
    if sys.platform != "linux" or os.environ.get("DISPLAY"):
        return bool(os.environ.get("DISPLAY"))

    with _xvfb_lock:
        if os.environ.get("DISPLAY"):
            return True
        display = ":99"
        lock_file = "/tmp/.X99-lock"
        try:
            if not os.path.exists(lock_file):
                subprocess.Popen(
                    ["Xvfb", display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                time.sleep(1)
                logging.info("[Xvfb] 已在 Linux 环境启动虚拟桌面 (%s 1920x1080x24)", display)
            os.environ["DISPLAY"] = display
            return True
        except Exception as e:
            logging.debug("[Xvfb] 启动虚拟桌面异常: %s", e)
            return False


def start_xvfb_display():
    ensure_xvfb()


def create_proxy_extension(proxy: dict) -> str:
    parsed_url = urllib.parse.urlparse(proxy['url'])
    scheme = parsed_url.scheme
    host = parsed_url.hostname
    port = parsed_url.port
    username = proxy['username']
    password = proxy['password']
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 3,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "storage",
            "webRequest",
            "webRequestAuthProvider"
        ],
        "host_permissions": [
          "<all_urls>"
        ],
        "background": {
          "service_worker": "background.js"
        },
        "minimum_chrome_version": "76.0.0"
    }
    """

    background_js = """
    var config = {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "%s",
                host: "%s",
                port: %d
            },
            bypassList: ["localhost"]
        }
    };

    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }

    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        { urls: ["<all_urls>"] },
        ['blocking']
    );
    """ % (
        scheme,
        host,
        port,
        username,
        password
    )

    proxy_extension_dir = tempfile.mkdtemp()

    with open(os.path.join(proxy_extension_dir, "manifest.json"), "w") as f:
        f.write(manifest_json)

    with open(os.path.join(proxy_extension_dir, "background.js"), "w") as f:
        f.write(background_js)

    return proxy_extension_dir


def find_browser_executable() -> Optional[str]:
    global CHROME_EXE_PATH
    if CHROME_EXE_PATH is not None:
        return CHROME_EXE_PATH
    env_path = os.environ.get("BROWSER_PATH")
    if env_path and os.path.exists(env_path):
        CHROME_EXE_PATH = env_path
        return CHROME_EXE_PATH
    if os.path.exists("/usr/bin/chromium"):
        CHROME_EXE_PATH = "/usr/bin/chromium"
        return CHROME_EXE_PATH
    # linux pyinstaller bundle
    chrome_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrome', "chrome")
    if os.path.exists(chrome_path):
        CHROME_EXE_PATH = chrome_path
        return CHROME_EXE_PATH
    # windows pyinstaller bundle
    chrome_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrome', "chrome.exe")
    if os.path.exists(chrome_path):
        CHROME_EXE_PATH = chrome_path
        return CHROME_EXE_PATH
    try:
        import zendriver as zd
        exe = str(zd.core.config.find_executable())
        if exe and os.path.exists(exe):
            CHROME_EXE_PATH = exe
            return CHROME_EXE_PATH
    except Exception:
        pass
    return None


def get_chrome_exe_path() -> Optional[str]:
    return find_browser_executable()


def get_chrome_major_version() -> str:
    global CHROME_MAJOR_VERSION
    if CHROME_MAJOR_VERSION is not None:
        return CHROME_MAJOR_VERSION

    chrome_path = find_browser_executable()
    if not chrome_path:
        return ""

    if os.name == 'nt':
        try:
            complete_version = extract_version_nt_executable(chrome_path)
        except Exception:
            try:
                complete_version = extract_version_nt_registry()
            except Exception:
                complete_version = extract_version_nt_folder()
    else:
        try:
            process = os.popen(f'"{chrome_path}" --version')
            complete_version = process.read()
            process.close()
        except Exception:
            complete_version = ""

    CHROME_MAJOR_VERSION = complete_version.split('.')[0].split(' ')[-1] if complete_version else ""
    return CHROME_MAJOR_VERSION


def extract_version_nt_executable(exe_path: str) -> str:
    import pefile
    pe = pefile.PE(exe_path, fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    return pe.FileInfo[0][0].StringTable[0].entries[b"FileVersion"].decode('utf-8')


def extract_version_nt_registry() -> str:
    stream = os.popen(
        'reg query "HKLM\\SOFTWARE\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome"')
    output = stream.read()
    google_version = ''
    for letter in output[output.rindex('DisplayVersion    REG_SZ') + 24:]:
        if letter != '\n':
            google_version += letter
        else:
            break
    return google_version.strip()


def extract_version_nt_folder() -> str:
    for i in range(2):
        path = 'C:\\Program Files' + (' (x86)' if i else '') + '\\Google\\Chrome\\Application'
        if os.path.isdir(path):
            paths = [f.path for f in os.scandir(path) if f.is_dir()]
            for path in paths:
                filename = os.path.basename(path)
                pattern = r'\d+\.\d+\.\d+\.\d+'
                match = re.search(pattern, filename)
                if match and match.group():
                    return match.group(0)
    return ''


async def start_browser(proxy: dict = None):
    import zendriver as zd
    has_xvfb = ensure_xvfb()
    exe = find_browser_executable()

    env_headless = os.environ.get("HEADLESS")
    if env_headless is not None:
        is_headless_env = env_headless.lower() not in ("0", "false", "no")
        headless = is_headless_env and not has_xvfb and not os.environ.get("DISPLAY")
    else:
        # 默认：存在 DISPLAY（包括 Xvfb 虚拟桌面）时使用有头模式渲染以轻松通过 Cloudflare Turnstile
        headless = not has_xvfb and not os.environ.get("DISPLAY")

    browser_args = [
        "--window-size=1920,1080",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-search-engine-choice-screen",
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
    ]
    if platform.machine().startswith(('arm', 'aarch')):
        browser_args.append("--disable-gpu-sandbox")
    if headless:
        browser_args.extend(["--disable-gpu", "--disable-setuid-sandbox", "--no-zygote"])

    proxy_extension_dir = None
    if proxy and all(key in proxy for key in ['url', 'username', 'password']):
        proxy_extension_dir = create_proxy_extension(proxy)
        browser_args.append(f"--load-extension={os.path.abspath(proxy_extension_dir)}")
    elif proxy and 'url' in proxy:
        browser_args.append(f"--proxy-server={proxy['url']}")

    lang = os.environ.get('LANG', 'en-US')
    browser = await zd.start(
        browser_executable_path=exe,
        headless=headless,
        sandbox=False,
        lang=lang,
        browser_connection_max_tries=60,
        browser_connection_timeout=0.5,
        browser_args=browser_args
    )
    if proxy_extension_dir:
        browser._proxy_extension_dir = proxy_extension_dir
    return browser


async def stop_browser(browser):
    if browser:
        try:
            await browser.stop()
        except Exception:
            pass
        proxy_dir = getattr(browser, "_proxy_extension_dir", None)
        if proxy_dir and os.path.exists(proxy_dir):
            try:
                shutil.rmtree(proxy_dir)
            except Exception:
                pass


def get_user_agent(browser=None) -> str:
    global USER_AGENT
    if USER_AGENT is not None:
        return USER_AGENT

    async def _fetch_ua():
        nonlocal browser
        stop_after = False
        if browser is None:
            browser = await start_browser()
            stop_after = True
        try:
            page = browser.main_tab or await browser.get("about:blank")
            ua = await page.evaluate("navigator.userAgent")
            ua = re.sub('HEADLESS', '', ua, flags=re.IGNORECASE)
            return ua
        finally:
            if stop_after:
                await stop_browser(browser)

    try:
        USER_AGENT = run_async(_fetch_ua(), timeout=30)
        return USER_AGENT
    except Exception as e:
        logging.warning("Failed to get user agent from browser: %s", e)
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def object_to_dict(_object):
    json_dict = json.loads(json.dumps(_object, default=lambda o: o.__dict__))
    return {k: v for k, v in json_dict.items() if not k.startswith('__')}
