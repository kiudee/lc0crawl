# LC0 Opening Crawler
A simple little crawler which downloads networks from lczero.org, runs it on a series of positions and saves the result into an sqlite3 database.

## Quick Start
The dependencies you can install using [poetry](https://python-poetry.org/) as follows:

```shell
git clone https://github.com/kiudee/lc0crawl.git
cd lc0crawl
poetry install
```

The crawler itself is contained in `lc0crawl/main.py`. It will assume that a `lc0pro.exe` exists in the current folder and that a `database.db` file already exists.

To create the `database.db` file and the jobs for the crawler, use the Jupyter notebook `lc0crawl/Jobs_and_Plots.ipynb`.

Currently, there are many things hardcoded and could be improved. Pull requests are welcome!