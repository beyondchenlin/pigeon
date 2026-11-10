"""Build a clean environment for the official, non-root FIRERPA launcher."""


def launch_environment(parent, runtime_root, user_root):
    # A running service contains __DNTNEEDRESPAWN__ and interpreter/bootstrap
    # state. Inheriting those skips the launcher's required interpreter re-exec,
    # losing the user module path. Persisted properties remain the source of
    # device configuration; do not copy credentials or internal service flags.
    allowed = ('ANDROID_ROOT', 'ANDROID_DATA', 'ANDROID_ART_ROOT',
               'ANDROID_I18N_ROOT', 'ANDROID_TZDATA_ROOT', 'ANDROID_STORAGE',
               'ANDROID_ASSETS', 'BOOTCLASSPATH', 'SYSTEMSERVERCLASSPATH',
               'STANDALONE_SYSTEMSERVER_JARS', 'DEX2OATBOOTCLASSPATH',
               'LANG', 'LC_CTYPE', 'EXTERNAL_STORAGE')
    env = {key: parent[key] for key in allowed if key in parent}
    env.update(PATH='/system/bin:/system/xbin', SHELL='/system/bin/sh',
               HOME=user_root, USRDIR=user_root, ROOTDIR=runtime_root,
               LD_LIBRARY_PATH=runtime_root+'/lib', TMPDIR='/data/local/tmp')
    return env
