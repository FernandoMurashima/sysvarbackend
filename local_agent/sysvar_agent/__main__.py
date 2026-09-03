import argparse

from .host import run_agent

def main():
    parser = argparse.ArgumentParser(prog="sysvar_agent")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    run_agent(config_path=args.config, once=args.once)


if __name__ == "__main__":
    main()
