# nao-meshes

Aldebaran's NAO meshes, converted to OBJ with PNG textures, published as an encrypted archive for [nao-viewer](https://github.com/funwithagents/nao-viewer)'s `fetch-meshes` command.

> **NAO meshes © Aldebaran, licensed under [CC BY-NC-ND 4.0](LICENSE-meshes). Non-commercial use only.**
> This repository's MIT license covers its build script, not the meshes.

## Getting the meshes

Use nao-viewer:

```
nao-viewer fetch-meshes
```

It shows the license, asks you to type `yes`, then downloads a release archive from this repository and unlocks it on your machine. The archives are encrypted so that the meshes reach someone only after they accept the license. Aldebaran allows redistribution of the meshes on that condition. Nothing in this repository's git history contains a mesh.

## Why this repository exists

Aldebaran distributes the meshes only through a Linux x86_64 installer (`ros-naoqi/nao_meshes_installer`), as Collada (`.dae`) files. MuJoCo can't load Collada, and the installer doesn't run on macOS. This repository runs the installer and the conversion once, so nao-viewer's users need neither.

## What a release contains

A release is tagged `r<N>` (`r1`, `r2`, …): the number counts releases of this repository, not NAO versions. A release holds one mesh set, `V40`, the meshes the NAO V5 URDF uses. It has three assets, and is never modified once published:

| Asset | What |
| --- | --- |
| `nao-meshes-V40-r<N>.zip` | The meshes, ZipCrypto-encrypted |
| `LICENSE` | The CC BY-NC-ND 4.0 text, identical to `ros-naoqi/nao_meshes`'s `LICENSE` |
| `SHA256SUMS` | SHA-256 of the two files above |

Inside the archive, under `nao-meshes-V40-r<N>/`:

- `manifest.json` (`"format": 1`, with `"release"` and `"mesh_set": "V40"`) maps each visual mesh of the NAO URDF, keyed by its path under `nao_meshes/meshes/` (`V40/HeadPitch.dae`), to its parts. All 39 are present. Each part is `{"obj", "texture", "rgba"}`, with exactly one of `texture` (a PNG; the OBJ then has UVs) and `rgba` (a flat color) set.
- `meshes/*.obj`: one OBJ per source mesh and material. Each holds positions, normals and UVs for textured parts, with no `mtllib`. The geometry is in the source DAE's frame and units: apply the URDF's `<visual><origin>` and `scale="0.1 0.1 0.1"` exactly as for the DAE.
- `textures/*.png`, `NOTICE`, `LICENSE`.

Consumers pin a tag and the archive's SHA-256.

## Building a release (maintainer)

Needs [uv](https://docs.astral.sh/uv/), the `zip` command, and either Linux x86_64 or Docker. On macOS the installer runs in an arm64 Debian container under QEMU user-mode emulation, because Docker Desktop's Rosetta can't run it.

```
uv run build.py --release r1
```

1. Downloads Aldebaran's pinned installer (`naomeshes-0.6.7-linux-x64-installer.run`) and checks its MD5 and SHA-256.
2. Runs it in text mode on your terminal. **You read and accept Aldebaran's license there.** Nothing answers for you. When it asks for the Installation Directory, press Enter to keep the default: that's where `build.py` collects the meshes.
3. Converts the 39 meshes and checks the result: every mesh has parts, every texture exists, and the torso is torso-sized once scaled.
4. Writes the archive, `LICENSE` and `SHA256SUMS` into `build/out/`.

The install stays in `build/prefix/`, and `--prefix build/prefix` rebuilds from it without running the installer again. Then publish by hand:

```
gh release create r1 build/out/* --title r1 --notes "NAO meshes, CC BY-NC-ND 4.0, non-commercial"
```

and pin `r1` and the archive's SHA-256 in nao-viewer (`src/nao_viewer/meshes.py`). `build/` is git-ignored: never commit anything from it.
