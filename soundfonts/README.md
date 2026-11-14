# Sounds: what we use and why

## Used now: GeneralUser GS (SoundFont, 32 MB)
- By S. Christian Collins: https://www.schristiancollins.com/generaluser, repo https://github.com/mrbumpy409/GeneralUser-GS
- **License:** free to use without restriction for music creation, private or commercial, and allowed inside software projects (full text in `GeneralUser-GS_LICENSE.txt`, downloaded with the SoundFont).
- Why: General MIDI compatible (our MIDI program numbers work as-is), small, consistently ranked among the best free GM banks for orchestral sounds, and it renders from Python in seconds.
- The `.sf2` is **not committed** (it's in `.gitignore`). `music/render_audio.py` downloads it automatically the first time.

Instruments used (GM program → GeneralUser preset):
| part | program | preset |
|---|---|---|
| Temperature (melody) | 49 | Slow Strings (violin section, smoother than the solo-violin preset 40) |
| Rain | 45 | Pizzicato Strings |
| Dust | 0 | Grand Piano |
| Smoke | 57 | Trombone |
| Lightning | 47 | Timpani |
| CO₂ | 43 | Double Bass |

## Render
```bash
pip install numpy && pip install --no-deps tinysoundfont   # --no-deps: skips pyaudio, which we don't need
python music/orchestra.py simulation/output/sim_both_24f_1deg_ --zone india --render
# or any MIDI file:
python music/render_audio.py music/output/orchestra_india_all.mid --reverb 0.3
```
`--sf2 other.sf2` swaps in any other SoundFont.

## Researched alternatives (for later or for the web player)
| library | type | license | notes |
|---|---|---|---|
| **VSCO 2 Community Edition** (Versilian) | SFZ + WAV, ~3 GB | **CC0** | best-sounding free orchestral samples; needs an SFZ player (sfizz) or a custom sampler. Repo: `sgossner/VSCO-2-CE` |
| **Sonatina Symphonic Orchestra** | SFZ / SF2, ~500 MB | CC Sampling Plus 1.0 | classic free orchestra; maintained fork `peastman/sso` |
| Virtual Playing Orchestra | SFZ | mixed (SSO + VSCO2 + Iowa…) | best of the above combined, many articulations |
| MuseScore_General | SF2/SF3 | MIT | GM bank, also a good fallback |
| VCSL (Versilian Community Sample Library) | SFZ + WAV | CC0 | keys, percussion, world instruments |

Web player options: **SpessaSynth** (plays the same `.sf2` in the browser, so web and Python sound identical) or **smplr** (Web Audio sampler with soundfont instruments and a sampled Steinway).
