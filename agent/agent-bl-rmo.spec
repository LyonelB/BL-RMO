# PyInstaller : agent BL-RMO en dossier (un exe et _internal), installé par Inno Setup.
# Un dossier plutôt qu'un exe unique : rien à décompresser à chaque lancement, l'agent
# se relance lui-même pour chaque lecture netaudio.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
d, b, h = collect_all('netaudio')
datas += d
binaries += b
hiddenimports += h
hiddenimports += collect_submodules('rich') + collect_submodules('zeroconf') + collect_submodules('psutil')

# Outils natifs de netaudio inutiles ici (point de terminaison audio, hôtes JACK et PulseAudio)
SKIP = ('netaudio-endpoint', 'netaudio-host-')
datas = [x for x in datas if not any(s in x[0].replace('\\', '/').rsplit('/', 1)[-1] for s in SKIP)]
binaries = [x for x in binaries if not any(s in x[0].replace('\\', '/').rsplit('/', 1)[-1] for s in SKIP)]

a = Analysis(['agent_bl_rmo.py'], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=['tkinter', 'unittest', 'pydoc_data'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='agent-bl-rmo', console=True, upx=False,
          version='version_info.txt' if __import__('os').path.exists('version_info.txt') else None)
coll = COLLECT(exe, a.binaries, a.datas, name='agent-bl-rmo', upx=False)
