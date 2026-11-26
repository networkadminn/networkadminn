#!/bin/bash
# Remote audit: paste output for esstracker screenshot debugging.
set -e
echo "=== $(hostname) user=$(whoami) ==="
dpkg -s esstracker 2>/dev/null | grep -E '^(Version|Status)' || echo 'esstracker: not installed'
GS=$(pgrep -u "$(id -un)" -x gnome-shell 2>/dev/null | head -1)
if [ -n "$GS" ]; then
  echo "=== graphical env (from gnome-shell) ==="
  tr '\0' '\n' < "/proc/$GS/environ" | grep -E '^(XDG_SESSION_TYPE|WAYLAND_DISPLAY|DISPLAY)=' || true
else
  echo "=== no gnome-shell (headless or other DE) ==="
fi
echo "=== venv python ==="
if [ -e /opt/esstracker/venv/bin/python ]; then
  ls -la /opt/esstracker/venv/bin/python
  /opt/esstracker/venv/bin/python -c 'import sys; print("python", sys.executable)' 2>&1 || true
  /opt/esstracker/venv/bin/python -c 'import mss' 2>&1 && echo mss_ok || echo mss_missing
else
  echo no venv
fi
echo "=== agent ==="
pgrep -u "$(id -un)" -af 'timetrack-agent|timetrack.agent' | head -3 || echo none
echo "=== capture tools ==="
for b in grim gnome-screenshot import scrot; do command -v "$b" >/dev/null && echo "has $b" || true; done
if [ -n "$GS" ]; then
  ENV=$(tr '\0' '\n' < "/proc/$GS/environ" | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_RUNTIME_DIR|HOME|USER)=' | xargs)
  echo "=== mss test ==="
  env $ENV /opt/esstracker/venv/bin/python -c "
import sys
sys.path.insert(0,'/opt/esstracker/app')
try:
    from timetrack.platform.screenshot import capture_jpeg
    r=capture_jpeg(max_width=320)
    if not r:
        print('capture: FAILED (None)')
    else:
        from io import BytesIO
        from PIL import Image
        im=Image.open(BytesIO(r[0])).convert('RGB')
        px=list(im.getdata())
        avg=sum(sum(p) for p in px[:5000])/(min(5000,len(px))*3)
        print('capture: ok avg_rgb=%.1f (under 15 often black)' % avg)
except Exception as e:
    print('capture: error', e)
" 2>&1 || true
fi
