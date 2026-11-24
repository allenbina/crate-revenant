# Native Mixxx library preview

Crate Revenant appears in the Mixxx library sidebar. It uses the same middleware as the Scratch Live and desktop companions.

1. Select **Crate Revenant** in the library sidebar.
2. Enter the middleware URL and click **Connect**. If the middleware runs on the same computer and exposes the default loopback port, use `http://127.0.0.1:8087`. Enter the Crate Revenant API token only if the middleware requires one.
3. Double-click a source to browse its playlists, or use the Mixxx search box. Playlist results and search results show the source alongside each track.
4. Select a track and click **Load Deck 1** or **Load Deck 2**. Shift+Left and Shift+Right also work while the track table is focused. The first load downloads the file to the Mixxx profile cache, then cues the deck.

## Build from source

Apply [the native library patch](Crate-Revenant-Mixxx-2.5.6.patch) to a clean Mixxx 2.5.6 checkout. The patch adds a C++/Qt library feature compiled into Mixxx.

```sh
git clone --branch 2.5.6 https://github.com/mixxxdj/mixxx.git
cd mixxx
git apply /path/to/Crate-Revenant-Mixxx-2.5.6.patch
```

Follow the [upstream Mixxx build instructions](https://github.com/mixxxdj/mixxx/wiki/Compiling-Mixxx) for the target platform. On Windows x64, the preview was built in a Visual Studio 2022 x64 Developer Command Prompt with the Mixxx 2.5 x64 Windows dependency bundle and Ninja:

```bat
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DMIXXX_VCPKG_ROOT="C:\path\to\mixxx-deps" -DVCPKG_TARGET_TRIPLET=x64-windows -DQML=OFF -DBUILD_TESTING=OFF -DBUILD_BENCH=OFF
cmake --build build --target mixxx --parallel 4
```

The Windows x64 build has been tested with a live middleware connection, playlist browsing, and loading a Navidrome track onto a deck. A full runtime test on other platforms and architectures is pending.
