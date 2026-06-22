try:
    from cement.utils.version import get_version as cement_get_version
except ModuleNotFoundError:
    # simple fallback when installing
    # version: Tuple[int, int, int, str, int])
    def cement_get_version(version):
        if version[3] == 'final':
            return f'{version[0]}.{version[1]}.{version[2]}'
        if version[3] == 'dev':
            return f'{version[0]}.{version[1]}.{version[2]}.dev.{version[4]}'
        mapping = {'alpha': 'a', 'beta': 'b', 'rc': 'c'}
        return f'{version[0]}.{version[1]}.{version[2]}{mapping[version[3]]}{version[4]}'


VERSION = (1, 3, 0, 'final', 0)


def get_version(version=VERSION):
    return cement_get_version(version)
