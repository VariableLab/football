import asyncio
import httpx
import time
from collections import Counter

TARGET_URL = "https://football.nett.to"

async def test_endpoint(client, name, method, path):
    start = time.time()
    try:
        if method == "GET":
            resp = await client.get(f"{TARGET_URL}{path}", timeout=30.0)
        else:
            resp = await client.post(f"{TARGET_URL}{path}", timeout=30.0)
        duration = time.time() - start
        return name, resp.status_code, duration
    except Exception:
        return name, "ERROR", time.time() - start

async def run_stress_test(concurrency=20, total_requests=100):
    print(f"🚀 Starting Stress Test on {TARGET_URL}")
    print(f"🔥 Concurrency: {concurrency}, Total Requests: {total_requests}")
    
    # We will test the most heavy endpoints
    endpoints = [
        ("Match List", "GET", "/api/matches?status=jingcai"),
        ("AI Report (Cached)", "POST", "/api/advisor/report/31521"), 
        ("Full Strategy", "GET", "/api/matches/31521/strategy")
    ]
    
    results = []
    async with httpx.AsyncClient() as client:
        for i in range(total_requests):
            name, method, path = endpoints[i % len(endpoints)]
            # Run in batches
            if (i+1) % concurrency == 0:
                print(f"  - Progress: {i+1}/{total_requests}...")
            
            # Simple serial for now to avoid overloading too fast, but we want to simulate concurrent
            # We'll use semaphore for actual concurrency
            pass

    # Correct concurrency using semaphore
    sem = asyncio.Semaphore(concurrency)
    async def throttled_test(client, name, method, path):
        async with sem:
            return await test_endpoint(client, name, method, path)

    async with httpx.AsyncClient() as client:
        tasks = []
        for i in range(total_requests):
            name, method, path = endpoints[i % len(endpoints)]
            tasks.append(throttled_test(client, name, method, path))
        
        results = await asyncio.gather(*tasks)

    print("\n" + "="*40)
    print("📊 STRESS TEST REPORT")
    print("="*40)
    
    for name in sorted(set(r[0] for r in results)):
        subset = [r for r in results if r[0] == name]
        statuses = Counter(r[1] for r in subset)
        avg_time = sum(r[2] for r in subset) / len(subset)
        max_time = max(r[2] for r in subset)
        
        print(f"[{name}]")
        print(f"  - Success Rate: {statuses[200] / len(subset):.1%}")
        print(f"  - Avg Latency: {avg_time:.3f}s")
        print(f"  - Max Latency: {max_time:.3f}s")
        print(f"  - Status Codes: {dict(statuses)}")
        print("-" * 20)

if __name__ == "__main__":
    asyncio.run(run_stress_test(concurrency=20, total_requests=100))
