#!/usr/bin/env python3
"""
DRY Code Analyzer - Detects repeated code patterns in software projects
Helps identify violations of the "Don't Repeat Yourself" principle
"""

import os
import re
import hashlib
import argparse
from collections import defaultdict, Counter
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple
import difflib

@dataclass
class CodeBlock:
    """Represents a block of code with metadata"""
    content: str
    file_path: str
    start_line: int
    end_line: int
    hash_value: str

@dataclass
class DuplicateGroup:
    """Represents a group of duplicate code blocks"""
    blocks: List[CodeBlock]
    similarity_score: float
    line_count: int

class DRYAnalyzer:
    def __init__(self, 
                 min_lines: int = 5,
                 similarity_threshold: float = 0.8,
                 ignore_whitespace: bool = True,
                 ignore_comments: bool = True):
        self.min_lines = min_lines
        self.similarity_threshold = similarity_threshold
        self.ignore_whitespace = ignore_whitespace
        self.ignore_comments = ignore_comments
        
        # File extensions to analyze
        self.supported_extensions = {
            '.py', '.js', '.ts', '.java', '.cpp', '.c', '.cs', '.php',
            '.rb', '.go', '.rs', '.kt', '.swift', '.scala', '.jsx', '.tsx'
        }
        
        # Comment patterns for different languages
        self.comment_patterns = {
            'python': [r'#.*$'],
            'javascript': [r'//.*$', r'/\*.*?\*/'],
            'java': [r'//.*$', r'/\*.*?\*/'],
            'cpp': [r'//.*$', r'/\*.*?\*/'],
            'default': [r'//.*$', r'/\*.*?\*/', r'#.*$']
        }

    def normalize_code(self, code: str, file_extension: str = '') -> str:
        """Normalize code by removing comments and whitespace if configured"""
        normalized = code
        
        if self.ignore_comments:
            # Determine language and remove comments
            lang = self._get_language_from_extension(file_extension)
            patterns = self.comment_patterns.get(lang, self.comment_patterns['default'])
            
            for pattern in patterns:
                normalized = re.sub(pattern, '', normalized, flags=re.MULTILINE | re.DOTALL)
        
        if self.ignore_whitespace:
            # Normalize whitespace - replace multiple spaces/tabs with single space
            normalized = re.sub(r'\s+', ' ', normalized)
            normalized = normalized.strip()
        
        return normalized

    def _get_language_from_extension(self, extension: str) -> str:
        """Map file extension to language for comment detection"""
        mapping = {
            '.py': 'python',
            '.js': 'javascript', '.jsx': 'javascript', '.ts': 'javascript', '.tsx': 'javascript',
            '.java': 'java',
            '.cpp': 'cpp', '.c': 'cpp', '.cc': 'cpp', '.cxx': 'cpp',
            '.cs': 'cpp',  # C# uses similar comment syntax
        }
        return mapping.get(extension.lower(), 'default')

    def extract_code_blocks(self, file_path: str) -> List[CodeBlock]:
        """Extract code blocks from a file"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Warning: Could not read {file_path}: {e}")
            return []

        blocks = []
        file_extension = os.path.splitext(file_path)[1]
        
        # Extract overlapping blocks of minimum size
        for start_line in range(len(lines) - self.min_lines + 1):
            for end_line in range(start_line + self.min_lines, len(lines) + 1):
                block_lines = lines[start_line:end_line]
                content = ''.join(block_lines)
                
                # Skip blocks that are mostly empty or whitespace
                if len(content.strip()) < 50:  # Minimum meaningful content
                    continue
                
                normalized_content = self.normalize_code(content, file_extension)
                if len(normalized_content.strip()) < 20:
                    continue
                
                hash_value = hashlib.md5(normalized_content.encode()).hexdigest()
                
                block = CodeBlock(
                    content=content,
                    file_path=file_path,
                    start_line=start_line + 1,  # 1-based line numbers
                    end_line=end_line,
                    hash_value=hash_value
                )
                blocks.append(block)
        
        return blocks

    def find_exact_duplicates(self, blocks: List[CodeBlock]) -> Dict[str, List[CodeBlock]]:
        """Find blocks with identical normalized content"""
        hash_to_blocks = defaultdict(list)
        
        for block in blocks:
            hash_to_blocks[block.hash_value].append(block)
        
        # Only return groups with duplicates
        return {h: blocks for h, blocks in hash_to_blocks.items() if len(blocks) > 1}

    def find_similar_blocks(self, blocks: List[CodeBlock]) -> List[DuplicateGroup]:
        """Find blocks with similar content using fuzzy matching"""
        duplicate_groups = []
        processed_blocks = set()
        
        for i, block1 in enumerate(blocks):
            if id(block1) in processed_blocks:
                continue
                
            similar_blocks = [block1]
            processed_blocks.add(id(block1))
            
            for j, block2 in enumerate(blocks[i+1:], i+1):
                if id(block2) in processed_blocks:
                    continue
                
                # Calculate similarity
                similarity = difflib.SequenceMatcher(
                    None, 
                    self.normalize_code(block1.content),
                    self.normalize_code(block2.content)
                ).ratio()
                
                if similarity >= self.similarity_threshold:
                    similar_blocks.append(block2)
                    processed_blocks.add(id(block2))
            
            if len(similar_blocks) > 1:
                avg_lines = sum(b.end_line - b.start_line + 1 for b in similar_blocks) // len(similar_blocks)
                duplicate_groups.append(DuplicateGroup(
                    blocks=similar_blocks,
                    similarity_score=1.0 if len(similar_blocks) == 2 else 
                                   sum(difflib.SequenceMatcher(None, 
                                       self.normalize_code(similar_blocks[0].content),
                                       self.normalize_code(b.content)).ratio() 
                                       for b in similar_blocks[1:]) / (len(similar_blocks) - 1),
                    line_count=avg_lines
                ))
        
        return sorted(duplicate_groups, key=lambda g: (-len(g.blocks), -g.line_count))

    def analyze_project(self, project_path: str) -> Tuple[List[DuplicateGroup], Dict[str, int]]:
        """Analyze entire project for code duplication"""
        print(f"Analyzing project: {project_path}")
        
        all_blocks = []
        file_stats = defaultdict(int)
        
        # Walk through project directory
        for root, dirs, files in os.walk(project_path):
            # Skip common non-source directories
            dirs[:] = [d for d in dirs if d not in {'.git', '.svn', 'node_modules', '__pycache__', '.pytest_cache', 'venv', 'env'}]
            
            for file in files:
                file_path = os.path.join(root, file)
                file_extension = os.path.splitext(file)[1]
                
                if file_extension in self.supported_extensions:
                    file_stats['files_analyzed'] += 1
                    blocks = self.extract_code_blocks(file_path)
                    all_blocks.extend(blocks)
                    file_stats['blocks_extracted'] += len(blocks)
        
        print(f"Extracted {len(all_blocks)} code blocks from {file_stats['files_analyzed']} files")
        
        # Find duplicates
        duplicate_groups = self.find_similar_blocks(all_blocks)
        file_stats['duplicate_groups'] = len(duplicate_groups)
        file_stats['total_duplicated_blocks'] = sum(len(g.blocks) for g in duplicate_groups)
        
        return duplicate_groups, file_stats

    def generate_report(self, duplicate_groups: List[DuplicateGroup], stats: Dict[str, int]) -> str:
        """Generate a detailed report of findings"""
        report = []
        report.append("=" * 80)
        report.append("DRY CODE ANALYSIS REPORT")
        report.append("=" * 80)
        report.append("")
        
        # Summary statistics
        report.append("SUMMARY:")
        report.append(f"  Files analyzed: {stats.get('files_analyzed', 0)}")
        report.append(f"  Code blocks extracted: {stats.get('blocks_extracted', 0)}")
        report.append(f"  Duplicate groups found: {stats.get('duplicate_groups', 0)}")
        report.append(f"  Total duplicated blocks: {stats.get('total_duplicated_blocks', 0)}")
        report.append("")
        
        if not duplicate_groups:
            report.append("🎉 No significant code duplication found!")
            report.append("Your code appears to follow DRY principles well.")
            return "\n".join(report)
        
        # Detailed findings
        report.append("DETAILED FINDINGS:")
        report.append("-" * 40)
        
        for i, group in enumerate(duplicate_groups[:20], 1):  # Show top 20
            report.append(f"\n{i}. DUPLICATE GROUP (Similarity: {group.similarity_score:.2%})")
            report.append(f"   Lines per block: ~{group.line_count}")
            report.append(f"   Occurrences: {len(group.blocks)}")
            report.append("   Locations:")
            
            for block in group.blocks:
                rel_path = os.path.relpath(block.file_path)
                report.append(f"     - {rel_path}:{block.start_line}-{block.end_line}")
            
            # Show a sample of the duplicated code
            if group.blocks:
                sample_lines = group.blocks[0].content.strip().split('\n')[:5]
                report.append("   Sample code:")
                for line in sample_lines:
                    report.append(f"     | {line}")
                if len(group.blocks[0].content.strip().split('\n')) > 5:
                    report.append("     | ...")
        
        if len(duplicate_groups) > 20:
            report.append(f"\n... and {len(duplicate_groups) - 20} more duplicate groups")
        
        # Recommendations
        report.append("\n" + "=" * 80)
        report.append("RECOMMENDATIONS:")
        report.append("=" * 80)
        report.append("1. Extract common code into functions or methods")
        report.append("2. Use inheritance or composition for similar classes")
        report.append("3. Create utility modules for repeated functionality")
        report.append("4. Consider using design patterns like Template Method or Strategy")
        report.append("5. Use configuration files for repeated constant values")
        
        return "\n".join(report)

def main():
    parser = argparse.ArgumentParser(description='Analyze code for DRY principle violations')
    parser.add_argument('path', help='Path to the project directory to analyze')
    parser.add_argument('--min-lines', type=int, default=5, 
                       help='Minimum lines for a code block (default: 5)')
    parser.add_argument('--similarity', type=float, default=0.8,
                       help='Similarity threshold for fuzzy matching (default: 0.8)')
    parser.add_argument('--output', '-o', help='Output file for the report')
    parser.add_argument('--ignore-whitespace', action='store_true', default=True,
                       help='Ignore whitespace differences (default: True)')
    parser.add_argument('--ignore-comments', action='store_true', default=True,
                       help='Ignore comment differences (default: True)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.path):
        print(f"Error: Path '{args.path}' does not exist")
        return 1
    
    # Initialize analyzer
    analyzer = DRYAnalyzer(
        min_lines=args.min_lines,
        similarity_threshold=args.similarity,
        ignore_whitespace=args.ignore_whitespace,
        ignore_comments=args.ignore_comments
    )
    
    # Analyze project
    try:
        duplicate_groups, stats = analyzer.analyze_project(args.path)
        report = analyzer.generate_report(duplicate_groups, stats)
        
        # Output report
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
            print(f"Report saved to: {args.output}")
        else:
            print(report)
            
    except Exception as e:
        print(f"Error during analysis: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
