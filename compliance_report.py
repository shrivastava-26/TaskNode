import argparse
from ai.audit import report

parser = argparse.ArgumentParser()
parser.add_argument("--purge", action="store_true", help="Apply the demo's metadata retention policy")
report(parser.parse_args().purge)
