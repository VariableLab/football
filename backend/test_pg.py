import asyncio
import asyncpg

async def test_connection():
    # User is 'postgre', password is 'prefect'
    # Trying to connect to a default DB first to see if it works. Usually 'postgres' exists even if the user is different, but if it fails we try 'postgre'.
    dsn_primary = "postgres://postgre:prefect@129.146.124.72:5432/postgres"
    dsn_fallback = "postgres://postgre:prefect@129.146.124.72:5432/postgre"
    
    conn = None
    try:
        print(f"Attempting to connect to: {dsn_primary}")
        conn = await asyncpg.connect(dsn_primary, timeout=10)
    except Exception as e:
        print(f"Primary failed: {e}. Trying fallback...")
        try:
            conn = await asyncpg.connect(dsn_fallback, timeout=10)
        except Exception as e2:
            print(f"❌ Both connections failed. Last error: {e2}")
            return

    print("✅ Connection successful!")
    
    # Check existing databases
    dbs = await conn.fetch("SELECT datname FROM pg_database WHERE datistemplate = false;")
    print("\nExisting Databases on this server:")
    for db in dbs:
        print(f" - {db['datname']}")
        
    # Check if our target DB exists, if not create it
    target_db = 'wcanalytics'
    db_exists = any(db['datname'] == target_db for db in dbs)
    
    if not db_exists:
        print(f"\nTarget database '{target_db}' does not exist. Creating it now...")
        # Cannot execute CREATE DATABASE inside a transaction block in asyncpg easily, so we use execute directly
        await conn.execute(f'CREATE DATABASE {target_db}')
        print(f"✅ Database '{target_db}' created successfully.")
    else:
        print(f"\nTarget database '{target_db}' already exists.")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(test_connection())