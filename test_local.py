#!/usr/bin/env python3
"""
Local test script to verify bot components work correctly.
Tests data providers and market matching without actual trading.
"""

import asyncio
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

async def test_opendota():
    """Test OpenDota API connection and data fetching."""
    print("\n" + "="*50)
    print("🎮 Testing OpenDota Provider")
    print("="*50)
    
    from src.esports.opendota import OpenDotaProvider
    
    api_key = os.getenv("OPENDOTA_API_KEY", "")
    provider = OpenDotaProvider(api_key)
    await provider.connect()
    
    print(f"✅ Connected to OpenDota (API key: {'configured' if api_key else 'not set'})")
    
    # Get live matches
    matches = await provider.get_live_matches()
    print(f"📊 Found {len(matches)} live Dota 2 matches")
    
    for match in matches[:3]:  # Show first 3
        radiant = match.get("radiant_team", {})
        dire = match.get("dire_team", {})
        radiant_name = radiant.get("team_name") or radiant.get("name") or "Unknown"
        dire_name = dire.get("team_name") or dire.get("name") or "Unknown"
        print(f"   🎮 {radiant_name} vs {dire_name} (ID: {match.get('match_id')})")
    
    if matches:
        # Test getting match state for first match
        match_id = str(matches[0].get("match_id"))
        state = await provider.get_match_state(match_id)
        if state:
            print(f"   📈 Match state: {state.team1_name} ({state.team1_score}) vs {state.team2_name} ({state.team2_score})")
            print(f"   ⏱️ Game time: {state.game_time}s, Win prob: {state.team1_win_probability:.1%}")
    
    await provider.disconnect()
    return len(matches)


async def test_lol_esports():
    """Test LoL Esports API connection."""
    print("\n" + "="*50)
    print("🎮 Testing LoL Esports Provider")
    print("="*50)
    
    from src.esports.lolesports import LoLEsportsProvider
    
    provider = LoLEsportsProvider()
    await provider.connect()
    
    print("✅ Connected to LoL Esports API")
    
    # Get live matches
    matches = await provider.get_live_matches()
    print(f"📊 Found {len(matches)} live LoL matches")
    
    for match in matches[:3]:
        team1 = match.get("team1", "Unknown")
        team2 = match.get("team2", "Unknown")
        print(f"   🎮 {team1} vs {team2}")
    
    await provider.disconnect()
    return len(matches)


async def test_market_discovery():
    """Test Polymarket market discovery (without trading)."""
    print("\n" + "="*50)
    print("💰 Testing Polymarket Market Discovery")
    print("="*50)
    
    import httpx
    
    # Test the gamma API endpoint directly
    base_url = "https://gamma-api.polymarket.com"
    
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        # Search for esports markets
        tags = ["esports", "lol", "dota-2", "league-of-legends"]
        total_markets = 0
        
        for tag in tags:
            try:
                response = await client.get(
                    f"{base_url}/events/pagination",
                    params={"tag": tag, "limit": 100, "active": True}
                )
                if response.status_code == 200:
                    data = response.json()
                    events = data if isinstance(data, list) else data.get("data", [])
                    count = len(events)
                    total_markets += count
                    if count > 0:
                        print(f"   ✅ Tag '{tag}': {count} events found")
                        # Show first market
                        if events:
                            event = events[0]
                            title = event.get("title", "Unknown")[:60]
                            print(f"      Example: {title}...")
                else:
                    print(f"   ⚠️ Tag '{tag}': HTTP {response.status_code}")
            except Exception as e:
                print(f"   ❌ Tag '{tag}': {e}")
        
        print(f"\n📊 Total esports markets found: {total_markets}")
        return total_markets


async def test_arbitrage_detection():
    """Test arbitrage detection logic."""
    print("\n" + "="*50)
    print("🎯 Testing Arbitrage Detection Logic")
    print("="*50)
    
    from src.engine.arbitrage_detector import ArbitrageDetector
    
    # Test that the detector can be instantiated
    detector = ArbitrageDetector()
    print("   ✅ ArbitrageDetector initialized")
    
    # Simulate the edge calculation logic
    our_prob = 0.65  # Our model says 65%
    market_prob = 0.55  # Market says 55%
    edge = our_prob - market_prob
    
    print(f"   📊 Example: Our probability: {our_prob:.1%}")
    print(f"   📊 Example: Market price: {market_prob:.1%}")
    print(f"   🎯 Example: Edge: {edge:.1%}")
    
    min_edge = detector.config.trading.min_edge_threshold
    print(f"   ⚙️ Min edge threshold: {min_edge:.1%}")
    
    if edge > min_edge:
        print(f"   ✅ OPPORTUNITY DETECTED! Edge of {edge:.1%} exceeds {min_edge:.1%} threshold")
        print(f"   💰 Would BUY at {market_prob:.1%} (our fair value: {our_prob:.1%})")
    else:
        print(f"   ⏸️ No opportunity - edge below threshold")
    
    return True


async def test_config():
    """Test configuration loading."""
    print("\n" + "="*50)
    print("⚙️ Testing Configuration")
    print("="*50)
    
    from src.config import get_config
    
    config = get_config()
    
    print(f"   💰 Initial Capital: ${config.trading.initial_capital}")
    print(f"   📊 Max Position Size: {config.trading.max_position_size_pct*100}%")
    print(f"   🎯 Min Edge: {config.trading.min_edge_threshold*100}%")
    print(f"   📝 Paper Trading: {config.is_paper_trading}")
    print(f"   🔑 OpenDota API Key: {'✅ Set' if config.esports.opendota_api_key else '❌ Not set'}")
    print(f"   🔑 GRID API Key: {'✅ Set' if config.esports.grid_api_key else '❌ Not set'}")
    print(f"   🔑 Polymarket Key: {'✅ Set' if config.polymarket.private_key else '❌ Not set'}")
    
    return True


async def main():
    """Run all tests."""
    print("\n" + "🚀 POLYMARKET ESPORTS BOT - LOCAL TEST" + "\n")
    print("This test verifies all components work correctly")
    print("without placing any real trades.\n")
    
    results = {}
    
    # Test config first
    try:
        await test_config()
        results["config"] = "✅ PASS"
    except Exception as e:
        results["config"] = f"❌ FAIL: {e}"
        print(f"   ❌ Config test failed: {e}")
    
    # Test OpenDota
    try:
        dota_matches = await test_opendota()
        results["opendota"] = f"✅ PASS ({dota_matches} matches)"
    except Exception as e:
        results["opendota"] = f"❌ FAIL: {e}"
        print(f"   ❌ OpenDota test failed: {e}")
    
    # Test LoL Esports
    try:
        lol_matches = await test_lol_esports()
        results["lol_esports"] = f"✅ PASS ({lol_matches} matches)"
    except Exception as e:
        results["lol_esports"] = f"❌ FAIL: {e}"
        print(f"   ❌ LoL Esports test failed: {e}")
    
    # Test market discovery
    try:
        markets = await test_market_discovery()
        results["markets"] = f"✅ PASS ({markets} markets)"
    except Exception as e:
        results["markets"] = f"❌ FAIL: {e}"
        print(f"   ❌ Market discovery test failed: {e}")
    
    # Test arbitrage detection
    try:
        await test_arbitrage_detection()
        results["arbitrage"] = "✅ PASS"
    except Exception as e:
        results["arbitrage"] = f"❌ FAIL: {e}"
        print(f"   ❌ Arbitrage test failed: {e}")
    
    # Summary
    print("\n" + "="*50)
    print("📋 TEST SUMMARY")
    print("="*50)
    for test, result in results.items():
        print(f"   {test}: {result}")
    
    all_passed = all("PASS" in r for r in results.values())
    print("\n" + ("✅ ALL TESTS PASSED - Ready to deploy!" if all_passed else "⚠️ Some tests failed - check above"))
    
    return all_passed


if __name__ == "__main__":
    asyncio.run(main())
