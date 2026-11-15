# Sounds: what we use and why

## Main engine: VSCO 2 Community Edition (real orchestral recordings)
- By Versilian Studios: https://versilian-studios.com/vsco-community/, repo https://github.com/sgossner/VSCO-2-CE (branch `SFZ`)
- **License: CC0** (public domain). Use it any way you like, including in commercial works. The only request is not to sell the samples themselves as a sample library.
- We download **only the 188 samples our instruments use (~440 MB)**, once, into `soundfonts/vsco/` (not committed):
  ```bash
  pip install soundfile
  python music/sampler.py --download
  ```
- `music/sampler.py` is a small SFZ sampler (numpy): it picks the right recording per note and loudness, pitch-shifts it to the exact note, applies attack/release, and loops long sustained notes with a crossfade. Measured tuning: within ±6 cents (xylophone corrected by −15 cents, because its recordings are sharp).
- **Hybrid rendering** (`--engine auto`, the default): instruments with a VSCO recording use it; anything else (regional palettes, the anomaly-marker drums) uses GeneralUser GS. Each VSCO part is **loudness-matched to its SoundFont version** (within 0.1 dB), so the mix balance stays the same.
- Cost: about 10 s to render a 2-minute piece (versus about 4 s with the SoundFont only).

| part | GM program | VSCO 2 CE instrument |
|---|---|---|
| Temperature (chords) | 0 | Upright Piano |
| Rain (melody) | 48 | Violin Section sustain (vibrato); short notes use the real **Violin Section tremolo** samples |
| CO₂ (bass) | 43 | Solo Contrabass sustain |
| Dust (melody) | 73 | Flute sustain (vibrato) |
| Smoke (melody) | 60 | French Horn sustain |
| Lightning | 47 | Timpani (SoundFont: VSCO has no timpani mapped yet) |
| Snowfall (melody) | 9 | Glockenspiel (tuning corrected −15 cents) |
| Fires (swell) | 57 | Trombones (SoundFont: no VSCO recording mapped) |
| Night lights (swell) | 89 | Synth pad (SoundFont: electronic, no VSCO recording) |

Note: `GM_TO_VSCO` in `music/sampler.py` still maps program 13 to the Xylophone, a leftover from when lightning was a xylophone; no part of the orchestra uses program 13 now (lightning is the timpani, program 47).

`--engine sf2` forces the SoundFont only; `--engine vsco` downloads the samples if they're missing.

## Fallback: GeneralUser GS

(SoundFont, 32 MB)
- By S. Christian Collins: https://www.schristiancollins.com/generaluser, repo https://github.com/mrbumpy409/GeneralUser-GS
- **License:** free to use without restriction for music creation, private or commercial, and allowed inside software projects (full text in `GeneralUser-GS_LICENSE.txt`, downloaded with the SoundFont).
- Why: General MIDI compatible (our MIDI program numbers work as-is), small, consistently ranked among the best free GM banks for orchestral sounds, and it renders from Python in seconds.
- The `.sf2` is **not committed** (it's in `.gitignore`). `music/render_audio.py` downloads it automatically the first time.

Instruments used (GM program → GeneralUser preset):
| part | program | preset |
|---|---|---|
| Temperature (chords) | 0 | Grand Piano |
| CO₂ (bass) | 43 | Double Bass |
| Rain (melody) | 48 | Fast Strings (violins) |
| Dust (melody) | 73 | Flute |
| Smoke (melody) | 60 | French Horns |
| Lightning | 47 | Timpani |
| Snowfall (melody) | 9 | Glockenspiel |
| Fires (swell) | 57 | Trombone |
| Night lights (swell) | 89 | Pad 2 (warm) |

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

## Realistic mode: recordings of rain, fire, thunder ... (`soundfonts/ambience/`)
`python music/soundscape.py --download` (done in the Docker image) fetches 7 free recordings from GitHub at pinned
commits and prepares 12 clips (seamless loops, 5 thunderclaps cut from a storm, matched levels), about 30 MB. Not in
git. Sources: the [Blanket](https://github.com/rafaelmardojai/blanket) and [noixer](https://github.com/abetusk/noixer)
projects, which collected them from Freesound and SoundBible, and from OpenGameArt for the clock:

| clip | recording | license |
|---|---|---|
| rain_light | Rain by alex36917 (freesound.org/s/524605), edited for Blanket | **CC BY**: credit the author |
| rain_heavy, thunder_1..5 | Spring night rain 2 by ali@k (freesound.org/s/156049) | CC0 |
| fire_camp | Campfire by cagancelik (freesound.org/s/433783) | CC0 |
| fire_place | Fireplace by ezwa (soundbible.com/1543) | Public domain |
| wind | Wind by felix.blume (freesound.org/s/217506), edited for Blanket | CC0 |
| city | City by gezortenplotz (freesound.org/s/44796), edited for Blanket | **CC BY**: credit the author |
| tick_1..11 | Ticking clock by bart ([opengameart.org/content/ticking-clock](https://opengameart.org/content/ticking-clock)): 4 single ticks + 7 cut from the loop (`temperature_sound: clock`) | CC0 |

The street-lamp hum and the heartbeat (temperature) are synthesised; the clock is a recording. The licences are those listed by Blanket and noixer; Freesound could not be
reached to re-check them. The two CC BY sounds need a credit line wherever the audio is published (e.g. the webapp's
about page).
