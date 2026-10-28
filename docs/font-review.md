# Local typography review — AED-057

The owner permits replacing Classic's typography with modern open-source fonts.
The owner selected the editorial pairing: Liberation Serif prompts and Lato
supporting text. The Classic template and pinned Linux image use this pairing.
Lato uses SIL OFL; the Ubuntu candidate used the Ubuntu Font Licence. Existing
serif/sans-serif fallbacks remain available. Their licence/copyright files stay with private font
copies. Microsoft acquisition is optional and currently deferred.

Use only private font directories outside Git, never production state/credentials.
Reuse `container/fonts.compose.yaml` to mount `./fonts` read-only at
`/usr/local/share/fonts/aed`. Do not put proprietary font binaries into Git,
Docker build contexts, image layers, reports or CI artefacts. The positive
`.dockerignore` excludes font directories. No licence is automatically accepted.

## Acquisition choices

The [Ubuntu font family](https://design.ubuntu.com/font) is openly licensed by
Canonical; retain the [Ubuntu Font Licence](https://ubuntu.com/legal/font-licence)
and copyright notices. Lato's [upstream licence and release metadata](https://github.com/google/fonts/blob/main/ofl/lato/FONTLOG.txt)
identify SIL OFL 1.1; signed Debian/Ubuntu fonts-lato packages are another source.
These open fonts can be evaluated without obtaining Microsoft binaries.

If the owner later chooses Georgia/Arial, they must supply files whose actual
licence permits this container use, or explicitly approve acquisition themselves.
Mac/Windows installation alone is not evidence of transfer rights. Microsoft's
[redistribution FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq)
distinguishes rendered graphic output from distributing font software and directs
users of fonts supplied elsewhere to the accompanying product licence.

Debian trixie's optional contrib `ttf-mscorefonts-installer` downloads non-free
Core Fonts rather than containing the fonts itself. The owner must first read
and accept the applicable [Core Fonts EULA](https://corefonts.sourceforge.net/eula.htm).
It permits installation/use and imposes complete-copy/notices/EULA and non-profit
distribution conditions; alteration and renaming are restricted. It is not a
blanket licence to redistribute loose fonts in an application image. Do not use
noninteractive EULA preseeding or unattended installation. In particular,
[Debian 3.8.1 templates](https://sources.debian.org/src/msttcorefonts/3.8.1/debian/templates)
do **not** provide Ubuntu's familiar EULA acceptance prompt: empty download-directory
input can download automatically. Do not rely on apt asking permission for the
licence. Any approved install belongs in an ephemeral acquisition environment,
not the application image; retain upstream notices and licence in private storage.
No Microsoft package/fonts were downloaded during development.

## Inspection and specimens

`python -m ask_every_day.font_review inventory` records fontconfig family, style,
16.16 integer font version, collection index and SHA256 for regular/bold/italic/
bold-italic Liberation Serif and Lato. `--body-family Lato --header-family Lato` selects
other review families. `--require-mounted` refuses family/style substitution and
requires resolved files under the read-only private font mount. Names/hashes alone
cannot prove licensing or provenance; retain the acquisition/version evidence.

Run the module inside the reviewed image with networking disabled. For example,
from an isolated private review deployment with no production mounts:

```sh
docker compose -f compose.yaml -f fonts.compose.yaml run --rm --no-deps --entrypoint python publisher -m ask_every_day.font_review inventory --require-mounted
docker compose -f compose.yaml -f fonts.compose.yaml run --rm --no-deps --mount type=bind,src=/absolute/private-reviews,dst=/review --entrypoint python publisher -m ask_every_day.font_review render --output /review/new-specimen --body-family Lato --header-family Lato --require-mounted
```

The render command reads approved CSVs and generates only AED-0311 in a **new**
review directory. It never opens SQLite, loads credentials, stages or publishes,
or replaces a canonical image. Family overrides affect review SVGs only; they
are not yet a production typography setting. Preserve the original Mac JPEG.
Use `compare --reference original-mac.jpg --candidate specimen.jpg` to measure
exact pixel equality and mean absolute channel difference at1080×1350; matching
family names do not establish raster parity, especially older Core Fonts vs Mac.

Check line breaks, body/header weight, line spacing, highlight alignment, all four
margins and clipping at full size. Existing layout coordinates/line wrapping remain
unchanged for specimens; a wider font may need a later reviewed layout adjustment.
Do not approve a clipped specimen. Each report contains only metadata/hashes,
rendered text/images and SVG referencing family names; no font bytes are embedded.

## Consistency and authorization

For deployments using the accepted editorial families, use
`make -f deployment/Makefile desktop-run ARGS='--directory /private/deployment preview'`
(and the same wrapper for schedule-preview/status/run/enable/disable). It always
selects the same optional `fonts.compose.yaml`; partial font/overlay setup blocks.
With an overlay, it requires all eight mounted Liberation Serif/Lato styles before any
publisher command. No overlay keeps the existing open-source fallbacks. Interactive
commands still require `--interactive` and the original owner challenge.

Do not use direct Compose commands without the overlay in that deployment.
The standard image already supplies the accepted open fonts, so it needs no optional
font overlay. Review overrides do not modify production settings. Scheduler policy now includes resolved family/style/version/index/file
hashes on Linux as well as its existing rendered reference. Font selection/byte
changes invalidate previous authorization, including an unused italic style that
would not alter the regular specimen. Review and grant a fresh policy only after
acceptance; no schedule is enabled by this tooling.

## Modern open-source comparison

After the owner clarified that new open-source typography is welcome, three
network-disabled review specimens were generated: Lato/Lato, Ubuntu/Ubuntu and
Liberation Serif/Lato. These use private open-font mounts and the existing overlay,
not Microsoft files. The original Mac AED-0311 JPEG is retained for comparison.
All are 1080×1350, retain original line breaks, baseline spacing, highlights and
alignment, and have no detected edge clipping. Visual inspection confirms
comfortable margins; body weight/letterforms differ intentionally. Measured pixel
parity is false for all three. Reports include exact image hashes, channel
error metrics, ink bounds and resolved font metadata; they contain no font binaries.

The ignored local gallery is `output/modern-font-review/index.html`. The owner
selected Liberation Serif/Lato. The Classic template now uses that pairing and
Docker supplies both open font packages; no Microsoft fonts or licence acceptance
are needed. Sizes, positions, line breaks and highlights are unchanged. New images
use the selected typefaces; historical JPEGs/state are preserved. This code/font
change invalidates prior scheduler approval and needs a fresh reviewed grant before
live use. Freeze accepted font files while publishing. No schedule or publication
is activated by this change.
