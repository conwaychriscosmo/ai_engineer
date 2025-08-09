#!/usr/bin/env python3
"""
DRY Refactor Bot - Automated code deduplication and refactoring
Consumes reports from DRY analyzer and performs safe automated refactoring

WARNING: This tool modifies your source code. Always use version control!
"""

import os
import re
import ast
import json
import shutil
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional, Any
from dataclasses import dataclass, asdict
from collections import defaultdict
import difflib
import hashlib

@dataclass
class RefactorLocation:
    """Represents a location where code needs to be refactored"""
    file_path: str
    start_line: int
    end_line: int
    content: str
    function_name: str = ""
    class_name: str = ""

@dataclass
class RefactorPlan:
    """Represents a plan for refactoring duplicated code"""
    id: str
    duplicate_locations: List[RefactorLocation]
    extracted_function_name: str
    extracted_function_content: str
    target_file: str
    confidence_score: float
    refactor_type: str  # 'function_extraction', 'class_method', 'utility_module'
    safety_checks_passed: bool = False

@dataclass
class RefactorResult:
    """Results of a refactoring operation"""
    plan_id: str
    success: bool
    files_modified: List[str]
    backup_location: str
    error_message: str = ""
    lines_removed: int = 0
    function_created: str = ""

class SafetyChecker:
    """Performs safety checks before refactoring"""
    
    def __init__(self):
        self.python_keywords = {
            'False', 'None', 'True', '__peg_parser__', 'and', 'as', 'assert', 'async',
            'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else',
            'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
            'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
            'try', 'while', 'with', 'yield'
        }
    
    def check_syntax_validity(self, code: str, file_extension: str) -> Tuple[bool, str]:
        """Check if code has valid syntax"""
        try:
            if file_extension == '.py':
                ast.parse(code)
            # For other languages, we'd need different parsers
            # For now, do basic checks
            return True, ""
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        except Exception as e:
            return False, f"Parse error: {e}"
    
    def check_variable_dependencies(self, code: str, context_code: str = "") -> Tuple[bool, str]:
        """Check for undefined variables that might cause issues"""
        try:
            # Simple regex-based check for Python
            variables_used = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', code))
            variables_used = variables_used - self.python_keywords
            
            # Remove function calls and attributes
            variables_used = {v for v in variables_used if not re.search(rf'{v}\s*\(', code)}
            variables_used = {v for v in variables_used if not re.search(rf'\.\s*{v}', code)}
            
            # Check if variables are defined in context
            if context_code:
                defined_vars = set(re.findall(r'(\w+)\s*=', context_code))
                undefined_vars = variables_used - defined_vars
                if undefined_vars:
                    return False, f"Potentially undefined variables: {undefined_vars}"
            
            return True, ""
        except Exception as e:
            return False, f"Dependency check failed: {e}"
    
    def check_refactor_safety(self, plan: RefactorPlan) -> Tuple[bool, List[str]]:
        """Comprehensive safety check for a refactor plan"""
        issues = []
        
        # Check 1: Minimum confidence score
        if plan.confidence_score < 0.7:
            issues.append(f"Low confidence score: {plan.confidence_score}")
        
        # Check 2: Syntax validity
        valid, error = self.check_syntax_validity(
            plan.extracted_function_content, 
            os.path.splitext(plan.target_file)[1]
        )
        if not valid:
            issues.append(f"Syntax issue: {error}")
        
        # Check 3: Check for risky patterns
        risky_patterns = [
            r'__.*__',  # Magic methods
            r'sys\.exit',  # System exits
            r'os\.system',  # System calls
            r'exec\s*\(',  # Dynamic execution
            r'eval\s*\(',  # Dynamic evaluation
        ]
        
        for pattern in risky_patterns:
            if re.search(pattern, plan.extracted_function_content):
                issues.append(f"Contains risky pattern: {pattern}")
        
        # Check 4: Ensure locations don't overlap inappropriately
        locations_by_file = defaultdict(list)
        for loc in plan.duplicate_locations:
            locations_by_file[loc.file_path].append(loc)
        
        for file_path, locations in locations_by_file.items():
            locations.sort(key=lambda x: x.start_line)
            for i in range(len(locations) - 1):
                if locations[i].end_line >= locations[i + 1].start_line:
                    issues.append(f"Overlapping locations in {file_path}")
        
        return len(issues) == 0, issues

class CodeAnalyzer:
    """Analyzes code structure for better refactoring decisions"""
    
    def __init__(self):
        pass
    
    def extract_function_signature(self, code: str, lang: str = 'python') -> Tuple[str, List[str]]:
        """Extract function signature and parameters from code"""
        if lang == 'python':
            return self._extract_python_signature(code)
        else:
            return self._extract_generic_signature(code)
    
    def _extract_python_signature(self, code: str) -> Tuple[str, List[str]]:
        """Extract Python function signature"""
        try:
            # Find variables that might be parameters
            variables = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', code))
            
            # Remove keywords and likely non-parameters
            keywords = {'if', 'else', 'for', 'while', 'def', 'class', 'return', 'print', 'len', 'str', 'int', 'float'}
            variables = variables - keywords
            
            # Heuristic: variables used but not assigned are likely parameters
            assigned_vars = set(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=', code))
            potential_params = list(variables - assigned_vars)
            
            return "extracted_function", potential_params[:5]  # Limit to 5 params
        except:
            return "extracted_function", []
    
    def _extract_generic_signature(self, code: str) -> Tuple[str, List[str]]:
        """Generic signature extraction for other languages"""
        return "extracted_function", []
    
    def find_best_location_for_function(self, duplicate_locations: List[RefactorLocation]) -> str:
        """Determine the best file to place the extracted function"""
        # Strategy 1: If duplicates are in the same file, place function there
        files = [loc.file_path for loc in duplicate_locations]
        file_counts = defaultdict(int)
        for f in files:
            file_counts[f] += 1
        
        # If one file has multiple duplicates, prefer that
        max_count = max(file_counts.values())
        if max_count > 1:
            return [f for f, c in file_counts.items() if c == max_count][0]
        
        # Strategy 2: Prefer utility files or modules
        for file_path in files:
            if 'util' in file_path.lower() or 'helper' in file_path.lower() or 'common' in file_path.lower():
                return file_path
        
        # Strategy 3: Choose the first file alphabetically for consistency
        return sorted(files)[0]
    
    def generate_function_name(self, code: str, existing_names: Set[str]) -> str:
        """Generate a meaningful function name based on code content"""
        # Extract meaningful words from code
        words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_]*\b', code.lower())
        
        # Remove common words
        stop_words = {'the', 'and', 'or', 'if', 'else', 'for', 'while', 'return', 'def', 'class'}
        meaningful_words = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Try to create a name
        base_names = [
            '_'.join(meaningful_words[:3]) if meaningful_words else 'extracted_function',
            f"common_{meaningful_words[0]}" if meaningful_words else 'common_function',
            'refactored_function'
        ]
        
        for base_name in base_names:
            if base_name not in existing_names:
                return base_name
            
            # Try with suffix
            for i in range(1, 100):
                candidate = f"{base_name}_{i}"
                if candidate not in existing_names:
                    return candidate
        
        return f"extracted_function_{len(existing_names)}"

class DRYRefactorBot:
    """Main refactoring automation class"""
    
    def __init__(self, 
                 backup_dir: str = "./dry_refactor_backups",
                 dry_run: bool = False,
                 interactive: bool = True):
        self.backup_dir = Path(backup_dir)
        self.dry_run = dry_run
        self.interactive = interactive
        self.safety_checker = SafetyChecker()
        self.code_analyzer = CodeAnalyzer()
        self.existing_function_names: Set[str] = set()
        
        # Create backup directory
        self.backup_dir.mkdir(exist_ok=True)
    
    def parse_dry_report(self, report_content: str) -> List[RefactorPlan]:
        """Parse DRY analyzer report and create refactor plans"""
        plans = []
        
        # Simple regex-based parsing of the report format
        # This assumes the report format from our DRY analyzer
        duplicate_groups = re.findall(
            r'(\d+)\. DUPLICATE GROUP.*?Similarity: ([\d.%]+).*?Lines per block: ~(\d+).*?Occurrences: (\d+).*?Locations:(.*?)(?=Sample code:|$)',
            report_content,
            re.DOTALL
        )
        
        for group_num, similarity, lines, occurrences, locations_text in duplicate_groups:
            # Parse locations
            location_matches = re.findall(r'- ([^:]+):(\d+)-(\d+)', locations_text)
            
            if len(location_matches) < 2:  # Need at least 2 duplicates
                continue
            
            duplicate_locations = []
            for file_path, start_line, end_line in location_matches:
                # Try to read the actual content
                try:
                    with open(file_path.strip(), 'r', encoding='utf-8') as f:
                        file_lines = f.readlines()
                        content = ''.join(file_lines[int(start_line)-1:int(end_line)])
                        
                        duplicate_locations.append(RefactorLocation(
                            file_path=file_path.strip(),
                            start_line=int(start_line),
                            end_line=int(end_line),
                            content=content
                        ))
                except Exception as e:
                    print(f"Warning: Could not read content from {file_path}: {e}")
                    continue
            
            if len(duplicate_locations) >= 2:
                # Create refactor plan
                target_file = self.code_analyzer.find_best_location_for_function(duplicate_locations)
                function_name = self.code_analyzer.generate_function_name(
                    duplicate_locations[0].content, 
                    self.existing_function_names
                )
                self.existing_function_names.add(function_name)
                
                # Extract function signature
                func_name, params = self.code_analyzer.extract_function_signature(
                    duplicate_locations[0].content
                )
                
                # Create function content
                function_content = self._create_function_content(
                    function_name, 
                    params, 
                    duplicate_locations[0].content
                )
                
                plan = RefactorPlan(
                    id=f"refactor_{group_num}",
                    duplicate_locations=duplicate_locations,
                    extracted_function_name=function_name,
                    extracted_function_content=function_content,
                    target_file=target_file,
                    confidence_score=float(similarity.rstrip('%')) / 100,
                    refactor_type='function_extraction'
                )
                
                plans.append(plan)
        
        return plans
    
    def _create_function_content(self, func_name: str, params: List[str], original_code: str) -> str:
        """Create the extracted function content"""
        # Clean up the original code
        lines = original_code.strip().split('\n')
        
        # Remove common indentation
        if lines:
            min_indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
            lines = [line[min_indent:] if line.strip() else line for line in lines]
        
        # Create function
        param_str = ', '.join(params) if params else ''
        function_lines = [
            f"def {func_name}({param_str}):",
            f'    """Extracted common functionality"""'
        ]
        
        # Add the body with proper indentation
        for line in lines:
            if line.strip():
                function_lines.append(f"    {line}")
            else:
                function_lines.append("")
        
        return '\n'.join(function_lines)
    
    def create_backup(self, files_to_backup: List[str]) -> str:
        """Create backup of files before modification"""
        timestamp = hashlib.md5(str(files_to_backup).encode()).hexdigest()[:8]
        backup_path = self.backup_dir / f"backup_{timestamp}"
        backup_path.mkdir(exist_ok=True)
        
        for file_path in files_to_backup:
            if os.path.exists(file_path):
                backup_file = backup_path / os.path.basename(file_path)
                shutil.copy2(file_path, backup_file)
        
        return str(backup_path)
    
    def execute_refactor_plan(self, plan: RefactorPlan) -> RefactorResult:
        """Execute a single refactor plan"""
        try:
            # Safety checks
            safe, issues = self.safety_checker.check_refactor_safety(plan)
            if not safe:
                return RefactorResult(
                    plan_id=plan.id,
                    success=False,
                    files_modified=[],
                    backup_location="",
                    error_message=f"Safety checks failed: {'; '.join(issues)}"
                )
            
            # Get all files that will be modified
            files_to_modify = list(set([loc.file_path for loc in plan.duplicate_locations] + [plan.target_file]))
            
            # Create backup
            backup_location = self.create_backup(files_to_modify)
            
            if self.dry_run:
                return RefactorResult(
                    plan_id=plan.id,
                    success=True,
                    files_modified=files_to_modify,
                    backup_location=backup_location,
                    lines_removed=sum(loc.end_line - loc.start_line + 1 for loc in plan.duplicate_locations),
                    function_created=plan.extracted_function_name
                )
            
            # Interactive confirmation
            if self.interactive:
                print(f"\nRefactor Plan: {plan.id}")
                print(f"Function to create: {plan.extracted_function_name}")
                print(f"Files to modify: {files_to_modify}")
                print(f"Confidence: {plan.confidence_score:.2%}")
                
                response = input("Proceed with this refactor? (y/n/s for show code): ").lower()
                if response == 's':
                    print("\nExtracted function content:")
                    print("-" * 40)
                    print(plan.extracted_function_content)
                    print("-" * 40)
                    response = input("Proceed? (y/n): ").lower()
                
                if response != 'y':
                    return RefactorResult(
                        plan_id=plan.id,
                        success=False,
                        files_modified=[],
                        backup_location=backup_location,
                        error_message="User cancelled"
                    )
            
            # Execute the refactoring
            self._add_function_to_file(plan.target_file, plan.extracted_function_content)
            
            lines_removed = 0
            for location in plan.duplicate_locations:
                lines_removed += self._replace_code_with_function_call(location, plan.extracted_function_name)
            
            return RefactorResult(
                plan_id=plan.id,
                success=True,
                files_modified=files_to_modify,
                backup_location=backup_location,
                lines_removed=lines_removed,
                function_created=plan.extracted_function_name
            )
            
        except Exception as e:
            return RefactorResult(
                plan_id=plan.id,
                success=False,
                files_modified=[],
                backup_location=backup_location if 'backup_location' in locals() else "",
                error_message=str(e)
            )
    
    def _add_function_to_file(self, file_path: str, function_content: str):
        """Add the extracted function to the target file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find a good place to insert the function (after imports, before classes/functions)
        lines = content.split('\n')
        insert_line = 0
        
        # Skip imports and docstrings
        in_docstring = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                in_docstring = not in_docstring
            if not in_docstring and not stripped.startswith(('import ', 'from ', '#', '"""', "'''")):
                if stripped and not stripped.startswith(('def ', 'class ')):
                    insert_line = i
                    break
        
        # Insert the function
        lines.insert(insert_line, function_content)
        lines.insert(insert_line + 1, "")  # Add blank line
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    
    def _replace_code_with_function_call(self, location: RefactorLocation, function_name: str) -> int:
        """Replace duplicated code with function call"""
        with open(location.file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Calculate indentation of the original code
        original_line = lines[location.start_line - 1]
        indent = len(original_line) - len(original_line.lstrip())
        
        # Create function call
        function_call = ' ' * indent + f"{function_name}()\n"
        
        # Replace the lines
        lines_removed = location.end_line - location.start_line + 1
        lines[location.start_line - 1:location.end_line] = [function_call]
        
        with open(location.file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        return lines_removed
    
    def refactor_project(self, report_file: str) -> List[RefactorResult]:
        """Main method to refactor entire project based on DRY report"""
        print(f"🤖 DRY Refactor Bot starting...")
        print(f"📊 Reading report: {report_file}")
        
        if not os.path.exists(report_file):
            print(f"❌ Report file not found: {report_file}")
            return []
        
        with open(report_file, 'r', encoding='utf-8') as f:
            report_content = f.read()
        
        # Parse report and create plans
        plans = self.parse_dry_report(report_content)
        print(f"📋 Created {len(plans)} refactor plans")
        
        if not plans:
            print("ℹ️  No refactoring opportunities found in report")
            return []
        
        results = []
        successful_refactors = 0
        
        # Execute each plan
        for i, plan in enumerate(plans, 1):
            print(f"\n🔧 Executing plan {i}/{len(plans)}: {plan.id}")
            result = self.execute_refactor_plan(plan)
            results.append(result)
            
            if result.success:
                successful_refactors += 1
                print(f"✅ Success: Created {result.function_created}, removed {result.lines_removed} lines")
            else:
                print(f"❌ Failed: {result.error_message}")
        
        # Summary
        print(f"\n📈 REFACTORING COMPLETE")
        print(f"✅ Successful: {successful_refactors}/{len(plans)}")
        print(f"📁 Backups created in: {self.backup_dir}")
        
        if successful_refactors > 0:
            total_lines_removed = sum(r.lines_removed for r in results if r.success)
            print(f"📉 Total lines of code reduced: {total_lines_removed}")
            print(f"🎯 DRY principle violations fixed: {successful_refactors}")
        
        return results

def main():
    parser = argparse.ArgumentParser(description='Automated DRY refactoring bot')
    parser.add_argument('report', help='Path to DRY analyzer report file')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Show what would be done without making changes')
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run without asking for confirmation (dangerous!)')
    parser.add_argument('--backup-dir', default='./dry_refactor_backups',
                       help='Directory for backups (default: ./dry_refactor_backups)')
    parser.add_argument('--output', '-o', help='Save results to JSON file')
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.non_interactive:
        print("⚠️  WARNING: This tool will modify your source code!")
        print("📋 Make sure you have committed your changes to version control.")
        response = input("Continue? (y/n): ").lower()
        if response != 'y':
            print("Cancelled.")
            return 1
    
    # Initialize refactor bot
    bot = DRYRefactorBot(
        backup_dir=args.backup_dir,
        dry_run=args.dry_run,
        interactive=not args.non_interactive
    )
    
    # Execute refactoring
    results = bot.refactor_project(args.report)
    
    # Save results if requested
    if args.output:
        results_data = [asdict(result) for result in results]
        with open(args.output, 'w') as f:
            json.dump(results_data, f, indent=2)
        print(f"📄 Results saved to: {args.output}")
    
    return 0

if __name__ == "__main__":
    exit(main())
