#!/bin/bash
# 把仓库里的 iina/ 安装为本机的 IINA 插件。
# 安装 = 拷贝到 ~/Library/Application Support/com.colliderli.iina/plugins/<identifier>.iinaplugin/
# IINA 在启动时扫描这个目录，装完或改完需要重启播放器才会生效。
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
IDENTIFIER="local.bread-cheese-bread"
DEST="$HOME/Library/Application Support/com.colliderli.iina/plugins/$IDENTIFIER.iinaplugin"

# 只在确认路径合法后才做删除动作，避免误伤
case "$DEST" in
  *"/com.colliderli.iina/plugins/"*) ;;
  *) echo "插件目标路径异常，已中止：$DEST" >&2; exit 1 ;;
esac

mkdir -p "$DEST"
rm -rf "${DEST:?}/"*
# 这里显式列出要装的文件，避免把开发用的东西（源码、文档）一起拷进去
for f in Info.json global.js main.js probe.html; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DEST/"
done

echo "已安装到："
echo "  $DEST"
ls -1 "$DEST"
echo
echo "接下去：完全退出 IINA 再重新打开（Cmd+Q，只关窗口不够）。"
