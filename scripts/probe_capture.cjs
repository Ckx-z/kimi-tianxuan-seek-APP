// 截图能力真机自检（P6）：验证 Electron 的 desktopCapturer 在本机可用、
// 缩略图非空、尺寸与 JPEG 体积是否落在我们设定的上限内。
// 运行： cd webapp; npx electron ..\scripts\probe_capture.cjs
const { app, desktopCapturer, screen } = require('electron');

app.whenReady().then(async () => {
  try {
    const display = screen.getPrimaryDisplay();
    const scale = display.scaleFactor || 1;
    const size = {
      width: Math.min(Math.round(display.size.width * scale), 2560),
      height: Math.min(Math.round(display.size.height * scale), 1440),
    };
    console.log('display=', display.size.width + 'x' + display.size.height,
                'scaleFactor=', scale, 'target=', size.width + 'x' + size.height);
    const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: size });
    console.log('sources=', sources.length,
                sources.map((s) => s.name + '#' + s.display_id).join(' | '));
    if (!sources.length) { console.log('RESULT=FAIL 没有可捕获的屏幕'); app.quit(); return; }
    const target = sources.find((s) => String(s.display_id) === String(display.id)) || sources[0];
    const img = target.thumbnail;
    const px = img.getSize();
    const png = img.toDataURL();
    const jpgLen = img.toJPEG(85).length;
    console.log('isEmpty=', img.isEmpty(), 'size=', px.width + 'x' + px.height);
    console.log('png dataURL chars=', png.length, '(约', (png.length / 1024 / 1024).toFixed(2), 'MB)');
    console.log('jpeg bytes=', jpgLen, '(约', (jpgLen / 1024 / 1024).toFixed(2), 'MB)');
    const ok = !img.isEmpty() && px.width > 200 && jpgLen > 1000;
    console.log('RESULT=' + (ok ? 'OK' : 'FAIL'));
  } catch (err) {
    console.log('RESULT=FAIL', String((err && err.message) || err));
  }
  app.quit();
});
