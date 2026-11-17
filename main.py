"""Start the existing Discord application; importing this module has no side effect."""


def main():
    from courswatch.discord_app.bot import main as start
    return start()


if __name__ == '__main__':
    main()
