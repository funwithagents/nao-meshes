"""Build a nao-meshes release: Aldebaran's NAO meshes as OBJ + PNG, in an encrypted zip.

Run by the maintainer, in a terminal:

    uv run build.py --release r1

1. Gets Aldebaran's pinned installer (or --installer PATH) and checks its hashes.
2. Runs it in text mode on your terminal: you read and accept Aldebaran's license there.
   On Linux x86_64 it runs natively; elsewhere (macOS) in an arm64 Debian container under
   QEMU user-mode emulation (Docker Desktop's Rosetta can't run this installer).
3. Converts the 39 URDF visual meshes (Collada) to OBJ parts with PNG textures or flat colors.
4. Writes manifest.json, NOTICE and LICENSE, zips them with the password, writes SHA256SUMS.

The install is kept in build/prefix: `--prefix build/prefix` reuses it without running the
installer again. Publishing is by hand: `gh release create r1 build/out/*`.
"""

import argparse
import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import collada
import numpy as np
import trimesh
from PIL import Image

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

INSTALLER_NAME = "naomeshes-0.6.7-linux-x64-installer.run"
INSTALLER_URL = (
    "https://github.com/ros-naoqi/nao_meshes_installer/raw/"
    f"f2bf5251b232551f992c73740b128684ba3fe328/{INSTALLER_NAME}"
)
INSTALLER_MD5 = "5f897a3328f217bae6c0ed2a934e524c"
INSTALLER_SHA256 = "15baf4609b9efca07bcc6195d9bbdcc8cc31905a50835aed5386a3afcb45ff59"
LICENSE_URL = (
    "https://raw.githubusercontent.com/ros-naoqi/nao_meshes/"
    "7c5b9f3a880fe38552459214157dcf1969812f4c/LICENSE"
)
LICENSE_SHA256 = "993af1e3328c63ad28770c756d6133fcf2f76d5e48c4a0bf6720057b3d872310"

# The same constant as nao-viewer's meshes.py: a gate, not a secret.
PASSWORD = "nao-meshes:cc-by-nc-nd-4.0:accepted"

# debian:bookworm-slim (multi-arch index), for running the installer under QEMU.
DEBIAN_IMAGE = "debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587"

# Every <visual> mesh of the NAO URDF (ros-naoqi/nao_robot,
# nao_description/urdf/naoV50_generated_urdf/nao.urdf), relative to nao_meshes/meshes/.
VISUAL_MESHES = (
    "V40/HeadYaw.dae", "V40/HeadPitch.dae",
    "V40/LHipYawPitch.dae", "V40/LHipRoll.dae", "V40/LHipPitch.dae", "V40/LKneePitch.dae",
    "V40/LAnklePitch.dae", "V40/LAnkleRoll.dae",
    "V40/RHipYawPitch.dae", "V40/RHipRoll.dae", "V40/RHipPitch.dae", "V40/RKneePitch.dae",
    "V40/RAnklePitch.dae", "V40/RAnkleRoll.dae",
    "V40/Torso.dae",
    "V40/LShoulderPitch.dae", "V40/LShoulderRoll.dae", "V40/LElbowRoll.dae", "V40/LWristYaw.dae",
    "V40/RShoulderPitch.dae", "V40/RShoulderRoll.dae", "V40/RElbowRoll.dae", "V40/RWristYaw.dae",
    "V40/LFinger11.dae", "V40/LFinger12.dae", "V40/LFinger13.dae",
    "V40/LFinger21.dae", "V40/LFinger22.dae", "V40/LFinger23.dae",
    "V40/LThumb1.dae", "V40/LThumb2.dae",
    "V40/RFinger11.dae", "V40/RFinger12.dae", "V40/RFinger13.dae",
    "V40/RFinger21.dae", "V40/RFinger22.dae", "V40/RFinger23.dae",
    "V40/RThumb1.dae", "V40/RThumb2.dae",
)  # fmt: skip
URDF_SCALE = 0.1  # <mesh scale="0.1 0.1 0.1"> on every visual
# The installer's mesh set these come from (meshes/V40/), the one the V5 URDF uses.
MESH_SET = "V40"


def archive_name(release: str) -> str:
    """The archive's stem, which is also its top folder: nao-meshes-V40-r1."""
    return f"nao-meshes-{MESH_SET}-{release}"


class BuildError(Exception):
    pass


def download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def get_installer(local: Path | None) -> Path:
    data = local.read_bytes() if local else download(INSTALLER_URL)
    if hashlib.md5(data).hexdigest() != INSTALLER_MD5:
        raise BuildError("installer MD5 mismatch")
    if hashlib.sha256(data).hexdigest() != INSTALLER_SHA256:
        raise BuildError("installer SHA-256 mismatch")
    path = BUILD / INSTALLER_NAME
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def run_installer(installer: Path, prefix: Path) -> None:
    """Run the installer in text mode on this terminal; the maintainer accepts its license."""
    if prefix.exists():
        shutil.rmtree(prefix)
    if sys.platform == "linux" and platform.machine() == "x86_64":
        command = [str(installer), "--mode", "text", "--prefix", str(prefix)]
    else:
        if shutil.which("docker") is None:
            raise BuildError(
                "the installer needs Linux x86_64, or Docker to emulate it"
            )
        setup = (
            "set -e; dpkg --add-architecture amd64; apt-get update -qq >/dev/null; "
            "apt-get install -y -qq --no-install-recommends qemu-user libc6:amd64 >/dev/null; "
            f"qemu-x86_64 /work/{installer.name} --mode text --prefix /work/{prefix.name}"
        )
        command = [
            "docker", "run", "--rm", "-it", "--platform", "linux/arm64",
            "-v", f"{BUILD}:/work", DEBIAN_IMAGE, "sh", "-c", setup,
        ]  # fmt: skip
    print(
        "running Aldebaran's installer: read its license and accept it to continue.\n"
        "When it asks for the Installation Directory, press Enter to keep its default:\n"
        "the default is where build.py collects the meshes.\n"
    )
    subprocess.run(command, check=True)
    if not (prefix / "meshes").is_dir():
        raise BuildError(
            f"the installer left no meshes/ in {prefix}: not accepted, or installed "
            "elsewhere (keep the default Installation Directory)"
        )


def _material(mesh: trimesh.Trimesh) -> tuple[Image.Image | None, list[float]]:
    """A part's texture image (with UVs) or its flat RGBA."""
    visual = mesh.visual
    if isinstance(visual, trimesh.visual.TextureVisuals):
        material = visual.material
        if isinstance(material, trimesh.visual.material.PBRMaterial):
            material = material.to_simple()
        image = getattr(material, "image", None)
        if (
            image is not None
            and visual.uv is not None
            and len(visual.uv) == len(mesh.vertices)
        ):
            return image, [1.0, 1.0, 1.0, 1.0]
        color = np.asarray(material.main_color, dtype=float)
    else:
        color = np.asarray(visual.main_color, dtype=float)
    return None, [round(float(c) / 255, 4) for c in color[:4]]


def write_obj(path: Path, meshes: list[trimesh.Trimesh], textured: bool) -> None:
    """Positions, normals and (textured) UVs; no mtllib: materials live in the manifest."""
    lines: list[str] = []
    faces: list[str] = []
    offset = 1
    for mesh in meshes:
        lines += [f"v {x:.7g} {y:.7g} {z:.7g}" for x, y, z in mesh.vertices]
        lines += [f"vn {x:.6g} {y:.6g} {z:.6g}" for x, y, z in mesh.vertex_normals]
        if textured:
            lines += [f"vt {u:.7g} {v:.7g}" for u, v in mesh.visual.uv]  # type: ignore[union-attr]
        for face in mesh.faces + offset:
            a, b, c = (int(i) for i in face)
            faces.append(
                f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}"
                if textured
                else f"f {a}//{a} {b}//{b} {c}//{c}"
            )
        offset += len(mesh.vertices)
    path.write_text("\n".join(lines + faces) + "\n")


def convert(prefix: Path, folder: Path) -> dict[str, list[dict[str, object]]]:
    (folder / "meshes").mkdir(parents=True)
    (folder / "textures").mkdir()
    manifest: dict[str, list[dict[str, object]]] = {}
    for name in VISUAL_MESHES:
        path = prefix / "meshes" / name
        # The DAEs reach the installer's texture/ folder with ../ paths, which trimesh's
        # default resolver refuses: the install prefix is ours, so allow them.
        resolver = trimesh.resolvers.FilePathResolver(path, allow_anywhere=True)
        scene = trimesh.load(path, force="scene", resolver=resolver)
        groups: dict[
            tuple[object, ...],
            tuple[Image.Image | None, list[float], list[trimesh.Trimesh]],
        ] = {}
        for mesh in scene.dump(concatenate=False):
            if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
                continue
            image, rgba = _material(mesh)
            key: tuple[object, ...] = (
                ("texture", id(image)) if image is not None else ("rgba", *rgba)
            )
            groups.setdefault(key, (image, rgba, []))[2].append(mesh)
        if not groups:
            raise BuildError(f"{name}: no geometry")
        images = [image.path for image in collada.Collada(str(path)).images]
        if images and all(image is None for image, _, _ in groups.values()):
            raise BuildError(
                f"{name} references {images}, but no texture could be loaded"
            )
        stem = Path(name).stem
        parts: list[dict[str, object]] = []
        for index, (image, rgba, meshes) in enumerate(groups.values()):
            obj = f"meshes/{stem}_{index}.obj"
            write_obj(folder / obj, meshes, textured=image is not None)
            if image is not None:
                png = io.BytesIO()
                image.convert(
                    "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
                ).save(png, "PNG")
                texture = (
                    f"textures/{hashlib.sha256(png.getvalue()).hexdigest()[:16]}.png"
                )
                (folder / texture).write_bytes(png.getvalue())
                parts.append({"obj": obj, "texture": texture, "rgba": None})
            else:
                parts.append({"obj": obj, "texture": None, "rgba": rgba})
        manifest[name] = parts
        print(f"  {name}: {len(parts)} part(s)")
    return manifest


def check(
    prefix: Path, folder: Path, manifest: dict[str, list[dict[str, object]]]
) -> None:
    missing = [m for m in VISUAL_MESHES if not manifest.get(m)]
    if missing:
        raise BuildError(f"meshes without parts: {missing}")
    for parts in manifest.values():
        for part in parts:
            if part["texture"] and not (folder / str(part["texture"])).is_file():
                raise BuildError(f"missing texture {part['texture']}")
    torso = trimesh.load(prefix / "meshes" / "V40/Torso.dae", force="scene").dump(
        concatenate=True
    )
    size = (torso.bounds[1] - torso.bounds[0]) * URDF_SCALE
    if not all(0.05 <= s <= 0.30 for s in size):
        raise BuildError(
            f"Torso is {size} m once scaled by {URDF_SCALE}: a unit mix-up?"
        )
    print(f"  Torso, scaled: {np.round(size, 3)} m")


def package(
    release: str,
    prefix: Path,
    out: Path,
    manifest: dict[str, list[dict[str, object]]],
    folder: Path,
) -> None:
    license_text = download(LICENSE_URL)
    if hashlib.sha256(license_text).hexdigest() != LICENSE_SHA256:
        raise BuildError("license text SHA-256 mismatch")
    (folder / "LICENSE").write_bytes(license_text)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "release": release,
                "mesh_set": MESH_SET,
                "source": {
                    "installer": INSTALLER_NAME,
                    "installer_sha256": INSTALLER_SHA256,
                },
                "meshes": manifest,
            },
            indent=2,
        )
        + "\n"
    )
    (folder / "NOTICE").write_text(
        "NAO meshes © Aldebaran, licensed under Creative Commons\n"
        "Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0), see LICENSE.\n"
        "NON-COMMERCIAL use only.\n\n"
        f"Converted to OBJ and PNG, unchanged otherwise, from Aldebaran's {INSTALLER_NAME}\n"
        f"(SHA-256 {INSTALLER_SHA256}), distributed by ros-naoqi/nao_meshes_installer.\n"
    )
    archive = out / f"{archive_name(release)}.zip"
    subprocess.run(
        ["zip", "-q", "-r", "-X", "-e", "-P", PASSWORD, str(archive), folder.name],
        cwd=folder.parent,
        check=True,
    )
    (out / "LICENSE").write_bytes(license_text)
    sums = [
        f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}"
        for n in (archive.name, "LICENSE")
    ]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print("\n" + "\n".join(sums))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--release", required=True, help="the release tag, r<N>")
    parser.add_argument(
        "--installer", type=Path, help="a local copy of Aldebaran's installer"
    )
    parser.add_argument(
        "--prefix",
        type=Path,
        help="reuse an existing install instead of running the installer",
    )
    args = parser.parse_args(argv)
    if not (args.release.startswith("r") and args.release[1:].isdigit()):
        parser.error("--release must be r<N>")
    try:
        BUILD.mkdir(exist_ok=True)
        if args.prefix:
            prefix = args.prefix.resolve()
        else:
            prefix = BUILD / "prefix"
            run_installer(get_installer(args.installer), prefix)
        out = BUILD / "out"
        staging = BUILD / "staging"
        for path in (out, staging):
            if path.exists():
                shutil.rmtree(path)
        out.mkdir()
        folder = staging / archive_name(args.release)
        print("converting")
        manifest = convert(prefix, folder)
        check(prefix, folder, manifest)
        package(args.release, prefix, out, manifest, folder)
        shutil.rmtree(staging)
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        print(f"build.py: error: {exc}", file=sys.stderr)
        return 1
    print(f"\nrelease assets in {out}: gh release create {args.release} {out}/*")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
