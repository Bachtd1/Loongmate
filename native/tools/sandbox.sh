#!/bin/sh
# Dựng/làm mới sandbox trên máy: /userdata/lm-sandbox-c4/{pak,sd1,sd2,userdata}.
# pak = bản chép của engine hiện tại trong repo (KHÔNG đụng LoongMate.pak thật).
set -eu
HERE=$(cd "$(dirname "$0")/../.." && pwd)
SSH=${LM_SSH:-"ssh -i $HOME/.ssh/pocket1_loongmate -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p 2222 sshadmin@pocket1"}
R=/userdata/lm-sandbox-c4
$SSH "mkdir -p $R/pak/res"
tar -cf - -C "$HERE" engine | $SSH "rm -rf $R/pak/engine && tar -xf - -C $R/pak"
tar -cf - -C "$HERE" systems.json bios_hashes.json | $SSH "tar -xf - -C $R/pak/res && cp $R/pak/res/systems.json $R/pak/res/bios_hashes.json $R/pak/ 2>/dev/null; true"
tar -cf - -C "$HERE/native/tools" make_sandbox.py | $SSH "tar -xf - -C $R"
$SSH "$R/../loongmate-runtime/python/bin/python3 $R/make_sandbox.py $R"
