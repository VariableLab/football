#!/usr/bin/env python3
"""
Generate professional marketing cards from football project screenshots.
Uses macOS Quartz/CoreGraphics via PIL (if available) or sips fallback.
"""

import subprocess
import os
import sys

SCREENSHOTS_DIR = "/Users/liuxuran/Github/football/screenshots"
OUTPUT_DIR = "/Users/liuxuran/Github/football/promo-video/out/cards"

# Brand colors
BG_DARK = "#0a0a0a"
ACCENT_GOLD = "#c8a86e"
TEXT_WHITE = "#e8e4de"
TEXT_MUTED = "#9e9488"

def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode == 0

def create_card_with_html(ss_path, output_path, title, subtitle, badge_text):
    """Use a simple HTML+CSS approach rendered via macOS preview or just create a styled overlay."""
    # We'll create a composite using sips and a template
    # For now, create a simple overlay approach
    
    ss_name = os.path.basename(ss_path)
    
    # Create a macOS Automator workflow or use sips compositing
    # Since sips is limited, we'll use a Python approach with PIL if available
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(ss_path)
        
        # Convert to RGBA
        img = img.convert('RGBA')
        w, h = img.size
        
        # Create a card with dark borders
        card_w = w + 80
        card_h = h + 200
        card = Image.new('RGBA', (card_w, card_h), (10, 10, 10, 255))
        
        # Paste screenshot centered
        paste_x = 40
        paste_y = 60
        card.paste(img, (paste_x, paste_y), img)
        
        draw = ImageDraw.Draw(card)
        
        # Try to use a nice font
        try:
            title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
            subtitle_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
            badge_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except:
            title_font = ImageFont.load_default()
            subtitle_font = title_font
            badge_font = title_font
        
        # Badge text
        draw.text((40, 15), badge_text, fill=(200, 168, 110), font=badge_font)
        
        # Title
        draw.text((40, 40), title, fill=(232, 228, 222), font=title_font)
        
        # Subtitle
        draw.text((40, card_h - 50), subtitle, fill=(158, 148, 136), font=subtitle_font)
        
        # Gold accent line
        draw.line([(40, 55), (100, 55)], fill=(200, 168, 110, 255), width=2)
        
        card.save(output_path, 'PNG')
        print(f"Created: {output_path}")
        return True
        
    except ImportError:
        print("PIL not available, falling back to sips overlay")
        return False

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    cards = [
        {
            "ss": "1.png",
            "title": "WC Analytics",
            "subtitle": "football.nett.to",
            "badge": "LIVE DEMO",
        },
        {
            "ss": "2.png", 
            "title": "Prediction Engine",
            "subtitle": "31,402 matches · 462 teams",
            "badge": "BACKTEST",
        },
        {
            "ss": "3.png",
            "title": "Probability Calibration",
            "subtitle": "Platt scaling · EV calculation",
            "badge": "CALIBRATION",
        },
        {
            "ss": "4.png",
            "title": "Strategy Layer",
            "subtitle": "Kelly optimization · Risk filtering",
            "badge": "STRATEGY",
        },
    ]
    
    success_count = 0
    for card in cards:
        ss_path = os.path.join(SCREENSHOTS_DIR, card["ss"])
        output_path = os.path.join(OUTPUT_DIR, f"card-{card['badge'].lower()}.png")
        
        if os.path.exists(ss_path):
            if create_card_with_html(ss_path, output_path, card["title"], card["subtitle"], card["badge"]):
                success_count += 1
            else:
                # Fallback: just copy with branding filename
                subprocess.run(f"cp '{ss_path}' '{output_path}'", shell=True)
                print(f"Copied (no PIL): {output_path}")
                success_count += 1
        else:
            print(f"Missing: {ss_path}")
    
    print(f"\nTotal cards created: {success_count}/{len(cards)}")
    return success_count == len(cards)

if __name__ == "__main__":
    main()
