import { test, expect } from "@playwright/test";

test("WebRTC renders advancing video with diagnostics below the picture", async ({ page }) => {
  await page.goto("/");
  const video = page.locator("#camera");
  await expect(page.locator("#camera-status")).toContainText("Live · WebRTC", { timeout: 20000 });
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.videoWidth)).toBeGreaterThan(0);
  const first = await video.evaluate((el: HTMLVideoElement) => el.getVideoPlaybackQuality().totalVideoFrames);
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.getVideoPlaybackQuality().totalVideoFrames)).toBeGreaterThan(first + 10);
  await expect(page.locator("#camera-note")).toContainText("Simulated scene");
  await page.getByText("Camera diagnostics", { exact: true }).click();
  await expect(page.locator("#camera-timing")).toContainText("SIMULATED timing");
  const picture = await video.boundingBox();
  const diagnostic = await page.locator("#camera-timing").boundingBox();
  expect(diagnostic!.y).toBeGreaterThanOrEqual(picture!.y + picture!.height);
  await page.screenshot({ path: "test-results/camera-video.png", fullPage: true });
});

test("failed signaling is obvious, never requests JPEGs, and recovers", async ({ page }) => {
  const jpegRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/camera/preview")) jpegRequests.push(request.url()); });
  await page.route("**/camera/webrtc/offer", route => route.abort());
  await page.goto("/");
  await expect(page.locator("#camera-status")).toContainText("Camera unavailable", { timeout: 20000 });
  await expect(page.locator("#camera")).toBeHidden();
  await expect(page.locator("#camera-status")).toHaveAttribute("data-state", "error");
  await page.unroute("**/camera/webrtc/offer");
  await expect(page.locator("#camera-status")).toContainText("Live · WebRTC", { timeout: 20000 });
  expect(jpegRequests).toEqual([]);
});

test("stalled video is hidden and reconnects", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#camera-status")).toContainText("Live · WebRTC", { timeout: 20000 });
  await page.locator("#camera").evaluate((el: HTMLVideoElement) => {
    const stream = el.srcObject as MediaStream;
    stream.getTracks().forEach(track => track.stop());
  });
  await expect(page.locator("#camera-status")).toContainText("Camera stalled", { timeout: 5000 });
  await expect(page.locator("#camera")).toBeHidden();
  await expect(page.locator("#camera-status")).toContainText("Live · WebRTC", { timeout: 20000 });
});

test("abruptly closed viewers release capacity for new viewers", async ({ browser }) => {
  for (let i = 0; i < 6; i++) {
    const context = await browser.newContext();
    try {
      const viewer = await context.newPage();
      await viewer.goto("http://127.0.0.1:5174/");
      await expect(viewer.locator("#camera-status")).toContainText("Live · WebRTC", { timeout: 15000 });
    } finally {
      // Context teardown does not reliably fire pagehide or deliver its cleanup request.
      await context.close();
    }
  }
});
