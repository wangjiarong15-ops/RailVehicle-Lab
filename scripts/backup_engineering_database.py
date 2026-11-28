"""Create or restore a verified RailVehicle-Lab database copy into a new path."""

from argparse import ArgumentParser

from rail_vehicle.engineering_backup import copy_verified_database


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    print(f"SHA-256 {copy_verified_database(args.source, args.target)}")


if __name__ == "__main__":
    main()
