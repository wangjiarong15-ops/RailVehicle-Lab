"""Extract factual numerical tables from the pinned public CONTACT testbank."""

from argparse import ArgumentParser
import json
from pathlib import Path

from rail_vehicle.contact_calibration import DATA, reference_bundle


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("testbank", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = reference_bundle(*( (args.testbank / name).read_bytes() for name in
                                 ("contact_mbench.ref_out","contact_nonhz.ref_out")))
    if args.check:
        if json.loads(DATA.read_text(encoding="utf-8")) != result:
            raise SystemExit("Derived CONTACT reference does not match the two source digests.")
    else:
        DATA.parent.mkdir(parents=True,exist_ok=True)
        with DATA.open("x",encoding="utf-8",newline="\n") as stream:
            json.dump(result,stream,indent=2,allow_nan=False)
            stream.write("\n")


if __name__ == "__main__":
    main()
