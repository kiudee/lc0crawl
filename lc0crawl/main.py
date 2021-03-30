import enum
import re
import sys
from decimal import Decimal
from pathlib import Path

import chess
import chess.engine
import pandas as pd
import requests
from bs4 import BeautifulSoup as BS
from sqlalchemy import (
    JSON,
    Column,
    Enum,
    Float,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import backref, relationship, sessionmaker

Base = declarative_base()


class PositionType(enum.Enum):
    FEN = 1
    MOVES = 2


class Job(Base):
    __tablename__ = "jobs"
    job_id = Column(Integer, autoincrement=True, unique=True)
    position = Column(String, nullable=False)
    network = Column(String, nullable=False)
    setting_hash = Column(String, nullable=False)
    position_type = Column(Enum(PositionType), nullable=False)
    settings = Column(JSON, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("position", "network", "setting_hash", name="job_pk"),
    )

    def __repr__(self):
        return "<Job({}, {}, {})>".format(
            self.position, self.network, self.position_type
        )


class Result(Base):
    __tablename__ = "results"
    position = Column(String)
    network = Column(String)
    setting_hash = Column(String)
    move = Column(String)
    q_value = Column(Float)
    policy = Column(Float)
    W = Column(Float)
    D = Column(Float)
    L = Column(Float)
    M = Column(Float)

    job = relationship(
        "Job",
        backref=backref("results", cascade="all"),
        foreign_keys=[position, network, setting_hash],
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "position", "network", "setting_hash", "move", name="result_pk"
        ),
        ForeignKeyConstraint(
            (position, network, setting_hash),
            (Job.position, Job.network, Job.setting_hash),
        ),
    )

    def __repr__(self):
        return "<Result({}, {}, {}, {}, {})>".format(
            self.position, self.network, self.move, self.q_value, self.policy
        )


def extract_table():
    r = requests.get("http://training.lczero.org/networks/?show_all=1")
    soup = BS(r.content, "html.parser")
    tbl = soup.find("table")
    records = []
    columns = []
    for tr in tbl.findAll("tr"):
        ths = tr.findAll("th")
        if len(ths) != 0:
            for each in ths:
                columns.append(each.text)
        else:
            trs = tr.findAll("td")
            record = []
            for col in trs:
                try:
                    link = col.find("a")["href"]
                    record.append(link)
                except:
                    text = col.text
                    record.append(text)
            records.append(record)
    df = pd.DataFrame(data=records, columns=columns)
    df = df.astype({"Number": "int32"})
    return df


def download_network(url, number):
    response = requests.get("http://training.lczero.org" + url)
    with open(f"{number}", "wb") as f:
        f.write(response.content)


def parse_info_line(s):
    matches = re.findall(r"\((\w+)\:\s*([\-\.0-9\%]*)\)", s)
    values = dict()
    for key, value in matches:
        if "-.-" in value:
            continue
        if key == "P":
            values[key] = float(Decimal(value.replace("%", "")) / 100)
        else:
            values[key] = float(value)
    move = re.findall(r"^[0-9a-z]+", s)[0]
    return move, values


def run_lc0_on_position(
    position, network, session, settings, setting_hash, is_fen=True
):
    if position == "" or position == " ":
        board = chess.Board()
    elif is_fen:
        board = chess.Board(position)
    else:
        board = chess.Board()
        for move in position.split(" "):
            board.push_san(move)

    # First extract the raw policy with 1.0 PST:
    lc0 = chess.engine.SimpleEngine.popen_uci("lc0pro.exe")
    lc0.configure(
        {
            "VerboseMoveStats": True,
            "NodesAsPlayouts": True,
            "SmartPruningFactor": 0.0,
            "PolicyTemperature": 1.0,
            "MinibatchSize": 1,
            "WeightsFile": network,
        }
    )
    info = lc0.analysis(board, chess.engine.Limit(nodes=1), info=chess.engine.INFO_ALL)
    results = dict()
    for line in info:
        print(line)
        if "string" in line and "node" not in line["string"]:
            move, values = parse_info_line(line["string"])
            results[move] = {
                "position": position,
                "network": network,
                "move": move,
                "policy": values["P"],
                "setting_hash": setting_hash,
                "q_value": None,
                "W": None,
                "D": None,
                "L": None,
                "M": None,
            }
    lc0.quit()
    print(results)

    # Now repeat the process using match parameters for 800 visits
    # to get the Q values
    lc0 = chess.engine.SimpleEngine.popen_uci("lc0pro.exe")
    lc0.configure(
        {
            **settings,
            **{
                "WeightsFile": network,
            },
        }
    )
    info = lc0.analysis(
        board, chess.engine.Limit(nodes=800), info=chess.engine.INFO_ALL
    )
    for line in info:
        if "string" in line and "node" not in line["string"]:
            move, values = parse_info_line(line["string"])
            d = values["D"]
            w = (values["WL"] - d + 1.0) / 2.0
            l = 1 - d - w
            results[move]["q_value"] = values["Q"]
            results[move]["W"] = w
            results[move]["D"] = d
            results[move]["L"] = l
            results[move]["M"] = values["M"]

    for move, values in results.items():
        res = Result(**values)
        session.add(res)
    lc0.quit()

    session.commit()


if __name__ == "__main__":
    engine = create_engine("sqlite:///database.db")
    Base.metadata.bind = engine
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()

    # To get the download links, we first crawl training.lczero.org/networks
    networks_df = extract_table()
    last_network = None

    while True:
        # Fetch the first job without results:
        job = (
            session.query(Job)
            .filter(Job.results == None)
            .order_by(Job.network.desc())
            .first()
        )
        if job is None:
            sys.exit(0)

        if not Path(f"./{job.network}").exists():
            # First delete the last network:
            try:
                Path(f"./{last_network}").unlink()
            except:
                pass
            # We first need to download the network:
            sub_df = networks_df[networks_df["Number"] == int(job.network)]
            try:
                uri = sub_df.Network.values[0]
            except IndexError:
                print(f"Network ID not found: {job.network}")
                raise
            download_network(url=uri, number=job.network)
            last_network = job.network

        run_lc0_on_position(
            position=job.position,
            network=job.network,
            session=session,
            settings=job.settings,
            setting_hash=job.setting_hash,
            is_fen=job.position_type == PositionType.FEN,
        )
