![Open Note Block Studio logo](https://i.imgur.com/AMd0NBh.png)

<p align="center">
  <a href="https://github.com/OpenNBS/OpenNoteBlockStudio/releases/latest">
    <img src="https://img.shields.io/github/v/release/OpenNBS/OpenNoteBlockStudio?label=latest" alt="Latest version">
  </a>
  <a href="https://github.com/OpenNBS/OpenNoteBlockStudio/releases/latest">
    <img src="https://img.shields.io/github/downloads/OpenNBS/OpenNoteBlockStudio/total">
  </a>
  <a href="https://github.com/OpenNBS/OpenNoteBlockStudio/stargazers">
    <img src="https://img.shields.io/github/stars/OpenNBS/OpenNoteBlockStudio?style=social">
  </a>
  <a href="https://discord.gg/w35BqQp">
    <img src="https://img.shields.io/discord/608692895179997252?label=discord&logo=discord&logoColor=white&color=7289DA">
  </a>
  <a href="http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=9iT2JIMOPWcrhzVpOROtlv7PJ1jSGEu0&authKey=VmmwRR7507A9fEVxraPmXn224vhbHXc4xs4QmwkveFZBriU%2Fq2NCj9fbYE5QCaLJ&noverify=0&group_code=218993157">
    <img src="https://img.shields.io/badge/QQ%E7%BE%A4-red?style=social&logo=qq">
  </a>
  <a href="https://github.com/OpenNBS/OpenNoteBlockStudio/blob/master/LICENSE">
    <img src="https://img.shields.io/github/license/OpenNBS/OpenNoteBlockStudio">
  </a>
</p>
<p align="center">
An open-source continuation of Minecraft Note Block Studio with exciting new features!
</p>

> [!IMPORTANT]
> **This is a personal, unofficial fork of Open Note Block Studio**, made for my own use. It is not made, endorsed
> or supported by the OpenNBS team, and it isn't supported by me either: there's no issue tracker and bugs that
> only affect this fork won't be fixed for other users. **Please don't report problems with this fork to OpenNBS**
> (their issue tracker or Discord). Only report something to them if it also happens in the
> [official release](https://github.com/OpenNBS/OpenNoteBlockStudio/releases/latest).

## 🍴 About this fork

This fork adds **note lengths**: any note can be held for a number of ticks instead of playing for an instant.
A held note's sound is stretched in time (not repeated), so it sounds like an instrument holding the note:

- Flute, didgeridoo and bit hold a steady level with a gentle vibrato.
- Other instruments, including custom sounds, ring out slowly.
- Drums (bass drum, snare, click) are never stretched.

Lengths play in the editor and in audio exports (WAV/MP3/OGG/FLAC). They're saved in `.nbs` files in a way the
official Note Block Studio and other tools safely ignore, so songs still open everywhere (without the lengths).
Minecraft exports (schematics, data packs) ignore lengths. See [NOTE_LENGTHS.md](NOTE_LENGTHS.md) for how to use it
and how it works.

It also bundles real-instrument sounds for custom instruments (piano, organ, strings, brass, woodwinds, mallets,
timpani, marching snare, crash cymbal) from [VSCO-2 Community Edition](https://github.com/sgossner/VSCO-2-CE)
(CC0), each tuned to a single note. They're in `Data/Sounds`.

It also includes a local command bridge (`scripts/mcp_bridge`) that lets a separate MCP server edit the open song.
It only accepts connections from your own computer.

There are no prebuilt downloads of this fork. It builds with GameMaker runtime **2022.6** (not the 2022.0.3
mentioned below); see [CLAUDE.md](CLAUDE.md) and [NOTE_LENGTHS.md](NOTE_LENGTHS.md).

---

*The rest of this README is from the official project and describes Open Note Block Studio itself.*

## ⬇ Download
You can download Open Note Block Studio [here](https://github.com/OpenNBS/OpenNoteBlockStudio/releases/latest)!

> _Support for macOS and Linux is in beta right now!_

> _We're having a TestFlight on macOS! Join [here](https://testflight.apple.com/join/hg58hwbM)!_

## 📄 Project Status

The current version is considered stable and feature-complete; as such, it's no longer accepting contributions or new features other than minor bug fixes.

In order to future-proof the project and allow us to make further improvements, we're currently in the process of rewriting the program using the Qt framework. You can read more about this and our future plans regarding the project in [this discussion](https://github.com/OpenNBS/OpenNoteBlockStudio/discussions/426)!

## 🐛 Report an issue

To report an issue with the official Open Note Block Studio or suggest an improvement, go to its [Issues](https://github.com/OpenNBS/OpenNoteBlockStudio/issues/new/choose) page! (Not for issues with this fork; see the note at the top.)

You can also join our [Discord server](https://discord.gg/w35BqQp) to get support and share your creations!

## ❤ Donate

If OpenNBS is meaningful to you and you'd like to support its development, you can support us through one of the links below!

[GitHub Sponsors](https://github.com/sponsors/OpenNBS) • [Open Collective](https://opencollective.com/OpenNBS)

###### Supporters
<img src="https://opencollective.com/opennbs/backers.svg" height="48px"/>

## 👥 Contributing

This project is written in the GameMaker Language (GML). You must have [GameMaker](https://gamemaker.io/) in order to open the project and make changes.

> **Note**: Active development takes place in the `development` branch. The `main` branch uses an older GameMaker runtime. If you're contributing, please use the `development` branch.

OpenNBS uses GameMaker runtime version 2022.0.3 LTS. Click on **Older Versions** at [this page](https://gms.yoyogames.com/ReleaseNotes-LTS.html) to see downloads.

If you'd like to contribute, please [open an issue](https://github.com/OpenNBS/OpenNoteBlockStudio/issues/new/choose) discussing your suggestion before making a pull request!

## ❔ More
For everything else, please visit our [website](https://opennbs.org/). You can also follow us on [Twitter](https://twitter.com/OpenNBS) and [YouTube](https://youtube.com/@OpenNBS)!
