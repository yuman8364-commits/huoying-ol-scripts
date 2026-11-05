"""Shared desktop UI, opening on the team and two-captain workflows."""
from hyol_client import UnifiedClient


def main():
    app = UnifiedClient()
    app.tabs.select(1)
    app.team_mode.set('已有队伍 → 多人单人')
    app._team_mode_changed()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
