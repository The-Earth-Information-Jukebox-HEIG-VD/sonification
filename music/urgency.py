"""The global CO2 record, which sets the tempo (`compose(..., tempo_source="co2")`, the default).

The urgency of a date is the global CO2 concentration of that year (the "earthbeat"), on a 0..1 scale from
290 ppm (1880, u = 0) to 430 ppm (May 2025, u = 1). CO2 mixes evenly in the atmosphere, so the same curve is
right for every zone. Unlike temperature, it almost never goes down: the music keeps speeding up.

Used by orchestra.compose:
    tempo      every genre: the genre's tempo x (0.8 + 0.45 u); realistic mode: a heartbeat of 60 + 75 u bpm
               (60 in 1880, about 132 today)
    realistic  the soundscape builds up too (soundscape.py): the heart turns irregular (skipped and extra beats)
               above 400 ppm, the temperature becomes a low heat drone that trembles and clashes more as it warms,
               and the whole soundscape gets louder with the CO2

CO2 record (ppm, annual means):
    1875-1958  Law Dome ice cores, Antarctica (20-year smoothed): Etheridge et al. 1998, CDIAC
    1959-2024  Mauna Loa Observatory, NOAA GML / Scripps (gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_mlo.txt)
    2025-2026  ESTIMATES (not yet published annual means): 2024 + the May-to-May rises measured at Mauna Loa
               (+3.6 ppm in 2025, +1.8 ppm in 2026, NOAA / Scripps). Beyond 2026 the last value is kept.
"""
from __future__ import annotations

import numpy as np

_LAW_DOME = """
1875 288.7 1876 289.0 1877 289.4 1878 289.8 1879 290.2 1880 290.7 1881 291.2 1882 291.7 1883 292.1 1884 292.6
1885 293.0 1886 293.3 1887 293.6 1888 293.8 1889 294.0 1890 294.2 1891 294.3 1892 294.5 1893 294.6 1894 294.7
1895 294.8 1896 294.9 1897 295.0 1898 295.2 1899 295.5 1900 295.8 1901 296.1 1902 296.5 1903 296.8 1904 297.2
1905 297.6 1906 298.1 1907 298.5 1908 298.9 1909 299.3 1910 299.7 1911 300.1 1912 300.4 1913 300.8 1914 301.1
1915 301.4 1916 301.7 1917 302.1 1918 302.4 1919 302.7 1920 303.0 1921 303.4 1922 303.8 1923 304.1 1924 304.5
1925 305.0 1926 305.4 1927 305.8 1928 306.3 1929 306.8 1930 307.2 1931 307.7 1932 308.2 1933 308.6 1934 309.0
1935 309.4 1936 309.8 1937 310.0 1938 310.2 1939 310.3 1940 310.4 1941 310.4 1942 310.3 1943 310.2 1944 310.1
1945 310.1 1946 310.1 1947 310.2 1948 310.3 1949 310.5 1950 310.7 1951 311.1 1952 311.5 1953 311.9 1954 312.4
1955 313.0 1956 313.6 1957 314.2 1958 314.9
"""
_MAUNA_LOA = """
1959 315.98 1960 316.91 1961 317.64 1962 318.45 1963 318.99 1964 319.62 1965 320.04 1966 321.37 1967 322.18
1968 323.05 1969 324.62 1970 325.68 1971 326.32 1972 327.46 1973 329.68 1974 330.19 1975 331.13 1976 332.03
1977 333.84 1978 335.41 1979 336.84 1980 338.76 1981 340.12 1982 341.48 1983 343.15 1984 344.87 1985 346.35
1986 347.61 1987 349.31 1988 351.69 1989 353.20 1990 354.45 1991 355.70 1992 356.54 1993 357.21 1994 358.96
1995 360.97 1996 362.74 1997 363.88 1998 366.84 1999 368.54 2000 369.71 2001 371.32 2002 373.45 2003 375.98
2004 377.70 2005 379.98 2006 382.09 2007 384.02 2008 385.83 2009 387.64 2010 390.10 2011 391.85 2012 394.06
2013 396.74 2014 398.81 2015 401.01 2016 404.41 2017 406.76 2018 408.72 2019 411.65 2020 414.21 2021 416.41
2022 418.53 2023 421.08 2024 424.61
"""
_ESTIMATES = {2025: 428.2, 2026: 430.0}     # 2024 + the measured May-to-May rises (see the docstring)


def _table():
    vals = []
    for block in (_LAW_DOME, _MAUNA_LOA):
        nums = block.split()
        vals += [(int(nums[i]), float(nums[i + 1])) for i in range(0, len(nums), 2)]
    vals += sorted(_ESTIMATES.items())
    years = np.array([y + 0.5 for y, _ in vals])      # an annual mean stands for the middle of its year
    return years, np.array([p for _, p in vals])


YEARS, PPM = _table()
PPM_LOW, PPM_HIGH = 290.0, 430.0          # u = 0 (1880) .. u = 1 (2025)
ESTIMATED_FROM = 2025                      # years from here on are estimates

def year_of(t) -> float:
    """'1880-01-15T00:00:00Z' / '1880-01' / 1880 -> 1880.04 (decimal year, middle of the month)."""
    s = str(t)
    try:
        y = int(s[:4])
    except ValueError:
        return float("nan")
    m = int(s[5:7]) if len(s) >= 7 and s[4] == "-" and s[5:7].isdigit() else 7
    return y + (m - 0.5) / 12


def co2_ppm(year: float) -> float:
    """Global CO2 (ppm) at a decimal year, interpolated in the record (kept constant outside it)."""
    return float(np.interp(year, YEARS, PPM))


def urgency_of_ppm(ppm: float) -> float:
    return float(np.clip((ppm - PPM_LOW) / (PPM_HIGH - PPM_LOW), 0.0, 1.0))


def urgency_of_times(times) -> float:
    """Mean urgency of the dates of a bar (0..1); NaN if none can be read."""
    ys = [year_of(t) for t in times]
    ys = [y for y in ys if np.isfinite(y)]
    return float(np.mean([urgency_of_ppm(co2_ppm(y)) for y in ys])) if ys else float("nan")


# ---- completing the team's CO2 maps with the world record ------------------------------------------------------
# The CO2 maps (NASA AIRS, SVS 5024) only cover 2002-09 -> 2022-03 and have blank areas. Where they have nothing,
# the CO2 track takes the world record above (CO2 mixes evenly, so it is right for every region), so its line, its
# music and its heartbeat cover 1875 -> today. The months that come from the maps are reported as "measured".
FIRST_MONTH = "1875-01"


def last_month() -> str:
    """The last month the world record is used for: the previous calendar month (UTC)."""
    import datetime as _dt
    d = _dt.datetime.now(_dt.timezone.utc).date().replace(day=1) - _dt.timedelta(days=1)
    return f"{d.year:04d}-{d.month:02d}"


def month_axis(first: str, last: str) -> list[str]:
    """'YYYY-MM' months from first to last, both included."""
    a, b = int(first[:4]) * 12 + int(first[5:7]) - 1, int(last[:4]) * 12 + int(last[5:7]) - 1
    return [f"{k // 12:04d}-{k % 12 + 1:02d}" for k in range(a, b + 1)]


def world_ppm(months) -> np.ndarray:
    """World CO2 (ppm) for each 'YYYY-MM' (the middle of the month)."""
    return np.array([co2_ppm(year_of(m)) for m in months], dtype=np.float64)


def complete_with_world(times, mean, first=FIRST_MONTH, last=None):
    """Monthly frames of the CO2 maps (ISO times, one value each; NaN = no data) -> the same on the axis
    first .. last (or their own ends if wider), the world record wherever the maps have no value.
    Returns (times as 'YYYY-MM-15T00:00:00Z', values, measured: bool per month)."""
    own = {str(t)[:7]: float(v) for t, v in zip(times, np.asarray(mean, dtype=np.float64))}
    months = sorted(own)
    axis = month_axis(min(first, months[0]) if months else first, max(last or last_month(), months[-1]) if months else (last or last_month()))
    vals = np.array([own.get(m, np.nan) for m in axis], dtype=np.float64)
    measured = np.isfinite(vals)
    vals[~measured] = world_ppm([m for m, ok in zip(axis, measured) if not ok])
    return np.array([f"{m}-15T00:00:00Z" for m in axis]), vals, measured


def completes_team_co2(var, source) -> bool:
    """True for the team's CO2 maps (NASA AIRS, 2002-09 -> 2022-03), the ones completed with the world record.
    Other CO2 datasets (simulated, or NOAA's in the development dataset) are played as they are."""
    return var == "co2" and "extracted by the team" in str(source or "")
