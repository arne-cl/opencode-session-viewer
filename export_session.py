# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Export OpenCode session data to JSON for use with the session viewer.

Usage:
    uv run export_session.py                     # Interactive: lists sessions to choose from
    uv run export_session.py <session_id>        # Export specific session
    uv run export_session.py --output out.json   # Specify output file (default: session_data.json)

Or run directly from GitHub:
    uv run https://raw.githubusercontent.com/ericmjl/opencode-session-viewer/main/export_session.py
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime


def get_db_path() -> Path:
    """Get the OpenCode SQLite database path."""
    return Path.home() / ".local/share/opencode/opencode.db"


def list_sessions(db_path: Path) -> list[dict]:
    """List all available sessions with metadata from SQLite."""
    sessions = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, title, directory, slug, version, "
        "summary_additions, summary_deletions, summary_files, "
        "time_created, time_updated, cost, "
        "tokens_input, tokens_output "
        "FROM session ORDER BY time_updated DESC"
    )

    for row in cursor.fetchall():
        sessions.append(
            {
                "id": row["id"],
                "title": row["title"],
                "directory": row["directory"],
                "slug": row["slug"],
                "version": row["version"],
                "summary": {
                    "additions": row["summary_additions"],
                    "deletions": row["summary_deletions"],
                    "files": row["summary_files"],
                },
                "time": {
                    "created": row["time_created"],
                    "updated": row["time_updated"],
                },
                "cost": row["cost"],
                "tokens_input": row["tokens_input"],
                "tokens_output": row["tokens_output"],
            }
        )

    conn.close()
    return sessions


def format_timestamp(ts: int) -> str:
    """Format a millisecond timestamp to human readable."""
    if not ts:
        return "Unknown"
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")


def export_session(db_path: Path, session_id: str) -> dict:
    """Export a session to a dictionary from SQLite."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM session WHERE id = ?", (session_id,))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Session not found: {session_id}")

    cursor.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
        (session_id,),
    )

    messages = []
    for msg_row in cursor.fetchall():
        msg = json.loads(msg_row["data"])
        msg["id"] = msg_row["id"]
        msg["sessionID"] = session_id

        cursor.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
            (msg_row["id"],),
        )
        msg["parts"] = [json.loads(p["data"]) for p in cursor.fetchall()]
        messages.append(msg)

    conn.close()

    return {
        "sessionID": session_id,
        "exportedAt": datetime.now().isoformat(),
        "messageCount": len(messages),
        "messages": messages,
    }


def _group_by_directory(sessions: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group sessions by directory, sorted by most recent update."""
    groups: dict[str, list[dict]] = {}
    for s in sessions:
        d = s.get("directory", "Unknown")
        groups.setdefault(d, []).append(s)
    for v in groups.values():
        v.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
    return sorted(groups.items(), key=lambda g: g[1][0].get("time", {}).get("updated", 0), reverse=True)


def _paginate(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    """Return (page_items, start_index, end_index)."""
    start = page * page_size
    end = min(start + page_size, len(items))
    return items[start:end], start, end


def _read_choice(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except KeyboardInterrupt:
        return None


def interactive_select(sessions: list[dict]) -> str | None:
    """Let user interactively select a session."""
    if not sessions:
        print("No sessions found.")
        return None

    page_size = 30
    dirs = _group_by_directory(sessions)

    # Step 1: select directory
    page = 0
    selected_dir = None
    while selected_dir is None:
        page_dirs, start, end = _paginate(dirs, page, page_size)
        print(f"\nDirectories (showing {start + 1}-{end} of {len(dirs)}):\n")
        print(f"{'#':<6} {'Sessions':>8}  {'Directory'}")
        print("-" * 80)
        for i, (directory, dir_sessions) in enumerate(page_dirs, start + 1):
            display_dir = directory if len(directory) <= 65 else "..." + directory[-62:]
            print(f"{i:<6} {len(dir_sessions):>8}  {display_dir}")

        print()
        prompt = "Enter # to select directory"
        if end < len(dirs):
            prompt += ", 'n' next"
        if page > 0:
            prompt += ", 'p' prev"
        prompt += ", 'q' quit: "
        choice = _read_choice(prompt)
        if choice is None or choice.lower() == "q":
            return None
        if choice.lower() == "n" and end < len(dirs):
            page += 1
            continue
        if choice.lower() == "p" and page > 0:
            page -= 1
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(dirs):
                selected_dir = idx
            else:
                print("Invalid selection.")
        except ValueError:
            print("Invalid input.")

    directory, dir_sessions = dirs[selected_dir]
    display_dir = directory if len(directory) <= 65 else "..." + directory[-62:]
    print(f"\nSessions in {display_dir}:\n")

    # Step 2: select session
    page = 0
    while True:
        page_sessions, start, end = _paginate(dir_sessions, page, page_size)
        print(f"{'#':<6} {'Updated':<18} {'Title'}")
        print("-" * 90)
        for i, session in enumerate(page_sessions, start + 1):
            updated = format_timestamp(session.get("time", {}).get("updated"))
            title = session.get("title", "Untitled")[:68]
            print(f"{i:<6} {updated:<18} {title}")

        print()
        prompt = "Enter session #"
        if end < len(dir_sessions):
            prompt += ", 'n' next"
        if page > 0:
            prompt += ", 'p' prev"
        prompt += ", 'b' back to dirs, 'q' quit: "
        choice = _read_choice(prompt)
        if choice is None or choice.lower() == "q":
            return None
        if choice.lower() == "b":
            return interactive_select(sessions)
        if choice.lower() == "n" and end < len(dir_sessions):
            page += 1
            continue
        if choice.lower() == "p" and page > 0:
            page -= 1
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(dir_sessions):
                return dir_sessions[idx]["id"]
            print("Invalid selection.")
        except ValueError:
            print("Invalid input.")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Export OpenCode session data to JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Interactive session selection
  %(prog)s ses_abc123...             # Export specific session
  %(prog)s --list                    # List all sessions
  %(prog)s --output my_session.json  # Custom output filename
        """,
    )
    parser.add_argument("session_id", nargs="?", help="Session ID to export")
    parser.add_argument(
        "--list", "-l", action="store_true", help="List available sessions"
    )
    parser.add_argument(
        "--output", "-o", default="session_data.json", help="Output filename"
    )

    args = parser.parse_args()

    db_path = get_db_path()

    if not db_path.exists():
        print(f"OpenCode database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # List sessions
    sessions = list_sessions(db_path)

    if args.list:
        if not sessions:
            print("No sessions found.")
        else:
            print(f"\nFound {len(sessions)} sessions:\n")
            for session in sessions[:50]:
                updated = format_timestamp(session.get("time", {}).get("updated"))
                print(f"  {session['id']}")
                print(f"    Title: {session.get('title', 'Untitled')}")
                print(f"    Directory: {session.get('directory', 'Unknown')}")
                print(f"    Updated: {updated}")
                print()
        sys.exit(0)

    # Get session ID
    session_id = args.session_id
    if not session_id:
        session_id = interactive_select(sessions)
        if not session_id:
            sys.exit(0)

    # Export
    print(f"Exporting session: {session_id}")

    try:
        data = export_session(db_path, session_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write output
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(data, f)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(
        f"Exported {data['messageCount']} messages to {output_path} ({size_mb:.1f} MB)"
    )
    print(f"\nTo view: open index.html and load {output_path}")


if __name__ == "__main__":
    main()
