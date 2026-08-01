# YouTube Spam Collection

The only third-party data in this repository. Everything else under
`examples/` was produced by this project's own seeded synthetic generator.

## Source and citation

> Alberto, T. & Lochter, J. (2015). *YouTube Spam Collection* [Dataset].
> UCI Machine Learning Repository. <https://doi.org/10.24432/C58885>

Downloaded from
<https://archive.ics.uci.edu/dataset/380/youtube+spam+collection>.

## Licence

**Creative Commons Attribution 4.0 International (CC BY 4.0)**, as stated on
the UCI dataset page: "This dataset is licensed under a Creative Commons
Attribution 4.0 International (CC BY 4.0) license." The licence permits sharing
and adaptation provided appropriate credit is given, which is what this file is
for. Full terms: <https://creativecommons.org/licenses/by/4.0/>.

The five CSV files are redistributed here **unmodified**, exactly as the archive
ships them. Nothing has been relabelled, filtered, resampled, or cleaned. If you
want to check that, the upstream archive is one download away.

## Shape

1,956 real YouTube comments from five videos, collected in 2013 to 2015.

| File | Rows |
|---|---|
| `Youtube01-Psy.csv` | 350 |
| `Youtube02-KatyPerry.csv` | 350 |
| `Youtube03-LMFAO.csv` | 438 |
| `Youtube04-Eminem.csv` | 448 |
| `Youtube05-Shakira.csv` | 370 |

Columns: `COMMENT_ID`, `AUTHOR`, `DATE`, `CONTENT`, `CLASS`.
`CLASS` is binary: 1 spam (1,005 rows), 0 legitimate (951 rows).

## What this data is used for here, and what it is not

Used for exactly one thing: pushing real, human-written comment text through
the input firewall in `examples/08-firewall-real-comments/`. See that
directory's `NOTES.md`.

It is **not** used to train anything, **not** used to evaluate any prompt, and
**not** wired into any measurement. It cannot be:

- It carries no account metadata, no registration attributes and no
  infrastructure hints, so **no pivot template can run against it** and it
  cannot produce an evidence pack.
- It carries no view or engagement events, so it cannot feed the VVR lens.
- Its labels are binary spam/ham, not this project's T-01 through T-07 threat
  classes. Mapping one onto the other would be inventing labels, so it is not
  done and the eval set stays synthetic.
- It has no planted rings and no ground-truth network, so it cannot feed the
  recovery metric.

The authors of this dataset are not affiliated with this project and have not
endorsed it.
