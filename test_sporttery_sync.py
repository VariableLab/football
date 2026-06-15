
import os
import sys

# Add backend subdirectories to path
_root = os.path.join(os.getcwd(), 'backend')
for d in ["api", "core", "features", "ingestion", "database", "strategy", "monitor", "utils", "api/routers"]:
    sys.path.append(os.path.join(_root, d))

from ingestion.sporttery_sync import sync_from_sporttery

def test_sync():
    print("Testing Sporttery Sync for World Cup matches...")
    result = sync_from_sporttery(days_ahead=3, generate_predictions=True)
    print("\nSync Summary:")
    print(result.summary())
    if result.errors:
        print("\nErrors:")
        for err in result.errors:
            print(f"- {err}")

if __name__ == "__main__":
    test_sync()
