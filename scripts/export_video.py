"""Frame-by-frame video export of the animation, via the Chrome DevTools Protocol.

Why not a screen recording: the result would depend on machine load (dropped
frames, uneven cadence). Here each frame is requested at a precise simulated
instant, and capture only happens once the frame has actually been drawn. The
result is deterministic and perfectly smooth, whatever the machine's real speed.

Requires Chrome started with --remote-debugging-port, and the local web server.
  python scripts/export_video.py [--speed 1800] [--fps 30] [--size 1920x1080]
"""
import argparse, asyncio, base64, json, subprocess, sys, time, urllib.request
import websockets

DAY = 86400


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr, await_promise=True):
        r = await self.call("Runtime.evaluate", expression=expr,
                            awaitPromise=await_promise, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "erreur JS"))
        return r["result"].get("value")


async def run(a):
    w, h = (int(x) for x in a.size.split("x"))
    frames = round(DAY / a.speed * a.fps)
    dt = DAY / frames
    print(f"cible: {frames} images, {frames/a.fps:.1f} s de video, {w}x{h} @ {a.fps} fps "
          f"(vitesse {a.speed}x)", flush=True)

    targets = json.load(urllib.request.urlopen(f"http://localhost:{a.port}/json"))
    page = next((t for t in targets if t["type"] == "page"), None)
    if not page:
        sys.exit("aucun onglet trouve: Chrome est-il lance avec --remote-debugging-port ?")

    async with websockets.connect(page["webSocketDebuggerUrl"],
                                  max_size=256 * 1024 * 1024) as ws:
        c = CDP(ws)
        await c.call("Page.enable")
        await c.call("Runtime.enable")
        await c.call("Emulation.setDeviceMetricsOverride",
                     width=w, height=h, deviceScaleFactor=1, mobile=False)
        await c.call("Page.navigate", url=f"{a.url}?render=1")

        # wait for trajectories to load, then for the basemap tiles
        for label, expr, limit in [
                ("donnees", "window.__ready === true", 180),
                ("tuiles",  "typeof map !== 'undefined' && map.loaded() && map.areTilesLoaded()", 90)]:
            t0 = time.time()
            while time.time() - t0 < limit:
                try:
                    if await c.js(expr, await_promise=False):
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.5)
            else:
                sys.exit(f"timeout en attendant: {label}")
            print(f"  {label} pretes ({time.time()-t0:.1f}s)", flush=True)

        # Warm-up: each hourly slice is sent to the GPU once, otherwise the first
        # frames of every hour stutter
        for b in range(24):
            await c.js(f"window.__seek({b*3600 + 1800})")
        print("  prechauffage GPU termine", flush=True)

        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe",
             "-framerate", str(a.fps), "-i", "-",
             "-c:v", "libx264", "-preset", "slow", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out],
            stdin=subprocess.PIPE)

        t0 = time.time()
        try:
            for i in range(frames):
                await c.js(f"window.__seek({i * dt})")
                shot = await c.call("Page.captureScreenshot", format="jpeg", quality=94)
                ff.stdin.write(base64.b64decode(shot["data"]))
                if i % 60 == 0 or i == frames - 1:
                    el = time.time() - t0
                    eta = el / (i + 1) * (frames - i - 1)
                    print(f"  image {i+1}/{frames}  ({100*(i+1)/frames:4.1f} %)  "
                          f"ecoule {el:5.0f}s  reste ~{eta:4.0f}s", flush=True)
        finally:
            ff.stdin.close()
            ff.wait()
        await c.call("Emulation.clearDeviceMetricsOverride")

    print(f"VIDEO_DONE {a.out}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--speed", type=int, default=1800)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--port", type=int, default=9222)
    p.add_argument("--url", default="http://localhost:8777/")
    p.add_argument("--out", default="specs/trafic-ferroviaire-france-24h.mp4")
    asyncio.run(run(p.parse_args()))
