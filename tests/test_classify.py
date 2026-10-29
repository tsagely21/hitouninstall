import os

from hito.core import categories as c
from hito.core.categories import Classifier, CustomCategory
from hito.core import winapi


def test_location_contexts():
    clf = Classifier()
    windir = os.environ.get("WINDIR", r"C:\Windows")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    assert clf.classify_path(os.path.join(windir, "System32", "kernel32.dll")) == c.WINDOWS
    assert clf.classify_path(os.path.join(pf, "App", "movie.mp4")) == c.APPS
    assert clf.classify_path(os.path.join(os.environ["TEMP"], "x", "y.tmp")) == c.TEMP
    assert clf.classify_path(os.path.join(windir, "Temp", "z.log")) == c.TEMP
    assert clf.classify_path(r"C:\pagefile.sys") == c.WINDOWS
    assert clf.classify_path(r"C:\$Recycle.Bin\S-1\file.doc") == c.RECYCLE


def test_appdata_caches_are_temp_but_settings_are_apps():
    clf = Classifier()
    local = os.environ["LOCALAPPDATA"]
    assert clf.classify_path(os.path.join(local, "SomeApp", "Cache", "data_1")) == c.TEMP
    assert clf.classify_path(os.path.join(local, "SomeApp", "settings.json")) == c.APPS


def test_user_folders_by_extension_and_known_folders():
    clf = Classifier()
    home = os.path.expanduser("~")
    assert clf.classify_path(os.path.join(home, "Projects", "song.flac")) == c.MUSIC
    assert clf.classify_path(os.path.join(home, "Projects", "archive.7z")) == c.ARCHIVES
    assert clf.classify_path(os.path.join(home, "Projects", "proj", "node_modules", "a.js")) == c.DEV
    assert clf.classify_path(os.path.join(winapi.known_folder("Downloads"), "setup.exe")) == c.DOWNLOADS
    assert clf.classify_path(os.path.join(home, "Projects", "unknown.qqq")) == c.OTHER


def test_game_roots():
    clf = Classifier(game_roots=[r"D:\SteamLibrary\steamapps"])
    assert clf.classify_path(r"D:\SteamLibrary\steamapps\common\Game\data.pak") == c.GAMES
    assert clf.classify_path(r"E:\XboxGames\Halo\Content\x.bin") == c.GAMES


def test_custom_category_extension_and_folder():
    clf = Classifier(custom=[CustomCategory("Design", "#fff", [".psd", "fig"], [r"D:\Work\Clients"])])
    home = os.path.expanduser("~")
    assert clf.classify_path(os.path.join(home, "Art", "poster.psd")) == "custom:Design"
    assert clf.classify_path(os.path.join(home, "Art", "board.fig")) == "custom:Design"
    assert clf.classify_path(r"D:\Work\Clients\a\video.mp4") == "custom:Design"
    # custom extensions do not override sealed system contexts
    windir = os.environ.get("WINDIR", r"C:\Windows")
    assert clf.classify_path(os.path.join(windir, "x.psd")) == c.WINDOWS
