# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['install-wizard.py'],
    pathex=[],
    binaries=[],
    datas=[('dist/Claude Hub.exe', '.'), ('session-context/qq-bridge/crypto_helper.py', 'session-context/qq-bridge'), ('tools/build-index.py', 'tools'), ('tools/qq-helper.py', 'tools'), ('tools/watch-queue.py', 'tools'), ('tools/find-file.py', 'tools'), ('tools/send-mail.py', 'tools'), ('tools/transcript.py', 'tools'), ('tools/gen_word.py', 'tools')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ClaudeHub-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['claude-hub-icon.ico'],
)
