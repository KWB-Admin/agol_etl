import polars, os, logging, requests, json
import psycopg2 as pg
from psycopg2 import sql
from numpy import ndarray
from datetime import datetime
from yaml import load, Loader
from log.logfilter import SensitiveFormatter

logging.basicConfig(
    filename="log/agol_etl.log",
    encoding="utf-8",
    filemode="a",
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M",
)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

for handler in logging.root.handlers:
    handler.setFormatter(
        SensitiveFormatter(
            "%(asctime)s - %(levelname)3s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
    )

user = os.getenv("kwb_dw_user")
host = os.getenv("kwb_dw_host")
password = os.getenv("kwb_dw_password")


def query_agol_data(token: str, survey_params: dict, date_ran: str):
    """
    This queryies survey layers in AGOL and dumps the returned results
    as a json.

    Args:
        token: str, API token as provided/generated by Esri
        survey_params: dict, dictionary of parameters used for specific survey query
        date_ran: str, date on which etl was ran
    """
    layer_url = survey_params["url"]

    layer_params = {
        "f": "pjson",
        "token": token,
    }
    try:
        layer_r = requests.get(layer_url, layer_params)
        if layer_r.status_code == 200:
            logger.info("Successfully queried %s data!" % (survey_params["name"]))
        else:
            logging.exception(
                "Bad request - check %s url and debug" % (survey_params["name"])
            )
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
    layer_json = layer_r.json()
    data_file_name = "raw_data/%s_%s.json" % (survey_params["name"], date_ran)
    with open(data_file_name, "w") as file:
        json.dump(layer_json, file, indent=4)


def transform_agol_data(survey_params, date_ran) -> polars.DataFrame:
    """
    This transforms json data into parquets with correct data format.

    Args:
        survey_params: dict, dictionary of parameters used for specific survey query
        date_ran: str, date on which etl was ran
    Returns:
        proccessed_data_path: str, path to parquet file to be loaded to db
    """
    schema = build_schema(survey_params)

    with open("raw_data/%s_%s.json" % (survey_params["name"], date_ran), "r") as file:
        data = json.load(file)

    df = (
        polars.from_dicts(
            [feature["attributes"] for feature in data["features"]], schema=schema
        )
        .with_columns(polars.from_epoch("date_collected", time_unit="ms"))
        .cast({"date_collected": polars.Date})
    )
    proccessed_data_path = ("processed_data/%s_%s.parquet") % (
        survey_params["name"],
        date_ran,
    )
    df.write_parquet(proccessed_data_path)
    return df


def build_schema(survey_params: dict) -> dict:
    """
    Builds schema based on survey_params input in etl_varibales.yaml
    Args:
        ssurvey_params: dict, dictionary of parameters used for specific survey query
    Returns:
        schema: dict, dictionary of colums with correct polars typing
    """
    schema: dict = survey_params["json_schema"]
    for key, value in schema.items():
        if value == "string":
            schema[key] = polars.String
        elif value == "int64":
            schema[key] = polars.Int64
        elif value == "float64":
            schema[key] = polars.Float64
        else:
            schema[key] = polars.String
    return schema


def get_pg_connection(db_name: str) -> pg.extensions.connection:
    """
    This tests a connection with a postgres database to ensure that
    we're loading into a database that actually exists.

    Args:
        db_name: str, name of database to connect to.
    Returns:
        con: pg.extensions.connection, psycopg connection to pg database
    """
    try:
        con = pg.connect(
            "dbname=%s user=%s host=%s password=%s" % (db_name, user, host, password)
        )
        con.autocommit = True
        logging.info("Successfully connected to %s db" % (db_name))
        return con

    except pg.OperationalError as Error:
        logging.error(Error)


def check_table_exists(con: pg.extensions.connection, schema_name: str, table: str):
    """
    This tests a to ensure the table we'll be writing to exists in
    the postgres schema provided.

    Args:
        con: pg.extensions.connection, psycopg connection to pg
            database
        schema_name: str, name of postgres schema
        table_name: str, name of table
    """
    cur = con.cursor()
    command = sql.SQL(
        """
        Select * from {schema_name}.{table} limit 1  
        """
    ).format(
        schema_name=sql.Identifier(schema_name),
        table=sql.Identifier(table),
    )
    try:
        cur.execute(command)
        if isinstance(cur.fetchall(), list):
            logging.info("Table exists, continue with loading.")
    except pg.OperationalError as Error:
        logging.error(Error)


def load_data_into_pg_warehouse(
    data: polars.DataFrame, etl_yaml: dict, survey_params: dict
):
    """
    This loads data into the KWB data warehouse, hosted in a postgres db.

    Args:
        data: polars.DataFrame, data to be loaded into warehouse
        etl_yaml: dict, general variables for the etl process
        survey_params: dict, variables for specific surveys, such as
            rainfall or recharge surveys
    """
    con = get_pg_connection(etl_yaml["db_name"])
    check_table_exists(con, etl_yaml["schema_name"], survey_params["table_name"])
    try:
        cur = con.cursor()
        for row in data.to_numpy():
            query = build_load_query(row, etl_yaml, survey_params)
            cur.execute(query)
        cur.close()
        con.close()
        logging.info(
            "Data was successfully loaded to %s.%s.%s"
            % (
                etl_yaml["db_name"],
                etl_yaml["schema_name"],
                survey_params["table_name"],
            )
        )
    except pg.OperationalError as Error:
        con.close()
        logging.error(Error)
    return


def build_load_query(
    data: ndarray, etl_yaml: dict, survey_params: dict
) -> pg.sql.Composed:
    """
    This loads data into the KWB data warehouse, hosted in a postgres db.

    Args:
        data: numpy.ndarray, row of data to be loaded
        etl_yaml: dict, general variables for the etl process
        survey_params: dict, variables for specific surveys, such as
            rainfall or recharge surveys
    Returns:
        pg.sql.Composed, Upsert query used to load data
    """
    col_names = sql.SQL(", ").join(
        sql.Identifier(col) for col in survey_params["db_schema"].keys()
    )
    values = sql.SQL(" , ").join(sql.Literal(val) for val in data)
    return sql.SQL(
        """
        INSERT INTO {schema_name}.{table} ({col_names}) VALUES ({values})
        ON CONFLICT ({prim_key}) DO UPDATE SET {update_col} = Excluded.{update_col}
        """
    ).format(
        schema_name=sql.Identifier(etl_yaml["schema_name"]),
        table=sql.Identifier(survey_params["table_name"]),
        col_names=col_names,
        values=values,
        prim_key=sql.SQL(survey_params["prim_key"]),
        update_col=sql.Identifier(survey_params["update_col"]),
    )


if __name__ == "__main__":
    date_ran = datetime.date(datetime.today())
    logger.info(
        "--------------- AGOL Survey ETL ran on %s ----------------" % (date_ran)
    )

    etl_yaml = load(open("yaml/etl_variables.yaml", "r"), Loader)
    token = etl_yaml["token"]
    for survey in etl_yaml["surveys"]:
        survey_params = etl_yaml["surveys"][survey]
        query_agol_data(token=token, survey_params=survey_params, date_ran=date_ran)

        proc_data = transform_agol_data(survey_params=survey_params, date_ran=date_ran)

        load_data_into_pg_warehouse(
            data=proc_data,
            etl_yaml=etl_yaml,
            survey_params=survey_params,
        )
    logger.info("Succesfully ran AGOL ETL.\n")
