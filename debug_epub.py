import ebooklib
from ebooklib import epub
import sys
import os

def analyze_epub(path):
    print(f"Analyzing: {path}")
    try:
        book = epub.read_epub(path)
        print("EPUB read successfully.")
        
        all_items = list(book.get_items())
        print(f"Total items: {len(all_items)}")
        
        images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
        print(f"Images (ITEM_IMAGE): {len(images)}")
        
        for item in all_items:
            print(f"Item: {item.get_name()} | Type: {item.get_type()} | MediaType: {item.media_type}")

    except Exception as e:
        print(f"Error reading EPUB: {e}")

if __name__ == "__main__":
    epub_path = r"C:\Users\ADMIN\Downloads\Seoul_Cyberpunk_Story.epub"
    if os.path.exists(epub_path):
        analyze_epub(epub_path)
    else:
        print(f"File not found: {epub_path}")
