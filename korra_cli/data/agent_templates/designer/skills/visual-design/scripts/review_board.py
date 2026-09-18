"""Build a comparison image for visual review without changing the inputs."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def build_board(candidate: Path, references: list[Path], output: Path) -> None:
    if not 1 <= len(references) <= 5:
        raise ValueError("Use one to five relevant reference images")
    if output.exists():
        raise FileExistsError("Choose a new output filename; existing files are preserved")
    inputs = [candidate, *references]
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("The output must differ from every input")
    images = []
    for path in inputs:
        with Image.open(path) as source:
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
            images.append(image)
    width, height = 2400, 2200
    board = Image.new("RGB", (width, height), "#eeeeee")
    draw = ImageDraw.Draw(board)
    font = ImageFont.load_default(size=28)

    def place(image: Image.Image, label: str, box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        draw.rectangle(box, fill="white")
        draw.text((left + 16, top + 12), label, fill="black", font=font)
        fitted = ImageOps.contain(image, (right - left - 32, bottom - top - 68))
        x = left + (right - left - fitted.width) // 2
        y = top + 52 + (bottom - top - 68 - fitted.height) // 2
        board.paste(fitted, (x, y))

    place(images[0], "RESULT", (20, 20, 1510, height - 20))
    row_height = (height - 40) // len(references)
    for index, image in enumerate(images[1:]):
        top = 20 + index * row_height
        place(image, f"SOURCE {index + 1}", (1530, top, width - 20, top + row_height - 16))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against a file appearing after the check.
    with output.open("xb") as handle:
        board.save(handle, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_board(args.candidate, args.reference, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
