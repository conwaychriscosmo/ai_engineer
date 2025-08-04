#!/usr/bin/env python3
"""
AI-Powered Full Stack Application Generator
Orchestrates the creation of a complete TypeScript/React application from user stories and vision.
"""

import json
import time
import asyncio
import subprocess
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml
import tempfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app_generator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ProjectConfig:
    """Configuration for the project generation"""
    budget_dollars: float
    vision: str
    user_stories: List[str]
    project_name: str
    output_dir: str
    api_key: str
    max_runtime_hours: int = 72
    cost_per_1k_tokens: float = 0.003  # Adjust based on your AI provider
    
@dataclass
class GenerationTask:
    """Represents a code generation task"""
    id: str
    type: str  # 'backend', 'frontend', 'database', 'tests', 'deployment'
    description: str
    dependencies: List[str]
    priority: int
    estimated_tokens: int
    status: str = "pending"  # pending, in_progress, completed, failed
    output_path: str = ""
    retry_count: int = 0

class BudgetManager:
    """Manages API budget and spending tracking"""
    
    def __init__(self, total_budget: float, cost_per_1k_tokens: float):
        self.total_budget = total_budget
        self.spent = 0.0
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.transactions = []
        
    def can_spend(self, estimated_tokens: int) -> bool:
        estimated_cost = (estimated_tokens / 1000) * self.cost_per_1k_tokens
        return self.spent + estimated_cost <= self.total_budget
        
    def record_spending(self, tokens_used: int, task_id: str):
        cost = (tokens_used / 1000) * self.cost_per_1k_tokens
        self.spent += cost
        self.transactions.append({
            'timestamp': datetime.now().isoformat(),
            'task_id': task_id,
            'tokens': tokens_used,
            'cost': cost
        })
        logger.info(f"Spent ${cost:.4f} on task {task_id}. Total: ${self.spent:.2f}/${self.total_budget:.2f}")
        
    def get_remaining_budget(self) -> float:
        return self.total_budget - self.spent

class AIOrchestrator:
    """Orchestrates AI API calls with budget management"""
    
    def __init__(self, api_key: str, budget_manager: BudgetManager):
        self.api_key = api_key
        self.budget_manager = budget_manager
        self.base_url = "https://api.anthropic.com/v1/messages"
        
    async def generate_code(self, task: GenerationTask, context: Dict) -> Tuple[str, int]:
        """Generate code for a specific task"""
        if not self.budget_manager.can_spend(task.estimated_tokens):
            raise Exception(f"Insufficient budget for task {task.id}")
            
        prompt = self._build_prompt(task, context)
        
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key
        }
        
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": min(4000, task.estimated_tokens),
            "messages": [{"role": "user", "content": prompt}]
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            content = result['content'][0]['text']
            tokens_used = result['usage']['input_tokens'] + result['usage']['output_tokens']
            
            self.budget_manager.record_spending(tokens_used, task.id)
            return content, tokens_used
            
        except Exception as e:
            logger.error(f"Failed to generate code for task {task.id}: {e}")
            raise
            
    def _build_prompt(self, task: GenerationTask, context: Dict) -> str:
        """Build a comprehensive prompt for code generation"""
        base_prompt = f"""
You are an expert full-stack developer. Generate production-ready code for the following task:

TASK: {task.description}
TYPE: {task.type}
PROJECT VISION: {context.get('vision', '')}

REQUIREMENTS:
- Use TypeScript for all code
- Follow best practices and include proper error handling
- Include comprehensive comments
- Generate complete, runnable code
- Include package.json dependencies if needed

CONTEXT:
{json.dumps(context.get('existing_structure', {}), indent=2)}

Please provide:
1. Complete code files with proper file structure
2. Installation/setup instructions
3. Any required configuration files
4. Brief explanation of the implementation

Focus on creating working, testable code that integrates well with the overall application architecture.
"""
        
        if task.type == 'backend':
            base_prompt += """
BACKEND SPECIFIC REQUIREMENTS:
- Use Express.js with TypeScript
- Include proper middleware setup
- Add input validation
- Include database integration (PostgreSQL preferred)
- Add proper logging
- Include health check endpoints
"""
        elif task.type == 'frontend':
            base_prompt += """
FRONTEND SPECIFIC REQUIREMENTS:
- Use React with TypeScript
- Include proper state management (Redux Toolkit or Context)
- Add responsive design with Tailwind CSS
- Include proper error boundaries
- Add loading states and proper UX
- Include proper routing with React Router
"""
        elif task.type == 'tests':
            base_prompt += """
TESTING REQUIREMENTS:
- Write comprehensive unit tests
- Include integration tests
- Use Jest and React Testing Library
- Add test coverage configuration
- Include E2E test examples with Playwright
"""
        elif task.type == 'deployment':
            base_prompt += """
DEPLOYMENT REQUIREMENTS:
- Create Docker configurations
- Include AWS/GCP deployment scripts
- Add CI/CD pipeline configuration
- Include environment variable management
- Add monitoring and logging setup
"""
            
        return base_prompt

class CodeValidator:
    """Validates and tests generated code"""
    
    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.temp_dir = None
        
    async def validate_code(self, code_content: str, file_path: str, task_type: str) -> Tuple[bool, List[str]]:
        """Validate generated code"""
        issues = []
        
        # Create temporary directory for validation
        with tempfile.TemporaryDirectory() as temp_dir:
            self.temp_dir = Path(temp_dir)
            
            try:
                # Write code to temporary file
                temp_file = self.temp_dir / file_path
                temp_file.parent.mkdir(parents=True, exist_ok=True)
                temp_file.write_text(code_content)
                
                # Run TypeScript compilation check
                if file_path.endswith('.ts') or file_path.endswith('.tsx'):
                    ts_issues = await self._check_typescript(temp_file)
                    issues.extend(ts_issues)
                
                # Run linting
                lint_issues = await self._run_linting(temp_file)
                issues.extend(lint_issues)
                
                # Run specific validation based on task type
                if task_type == 'backend':
                    backend_issues = await self._validate_backend(temp_file)
                    issues.extend(backend_issues)
                elif task_type == 'frontend':
                    frontend_issues = await self._validate_frontend(temp_file)
                    issues.extend(frontend_issues)
                    
            except Exception as e:
                issues.append(f"Validation error: {str(e)}")
                
        return len(issues) == 0, issues
        
    async def _check_typescript(self, file_path: Path) -> List[str]:
        """Check TypeScript compilation"""
        issues = []
        try:
            # Create minimal tsconfig.json
            tsconfig = {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "commonjs",
                    "strict": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "forceConsistentCasingInFileNames": True
                }
            }
            
            tsconfig_path = file_path.parent / "tsconfig.json"
            tsconfig_path.write_text(json.dumps(tsconfig, indent=2))
            
            # Run TypeScript compiler
            result = subprocess.run(
                ["npx", "tsc", "--noEmit", str(file_path)],
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                issues.append(f"TypeScript compilation errors: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            issues.append("TypeScript compilation timeout")
        except Exception as e:
            issues.append(f"TypeScript check failed: {str(e)}")
            
        return issues
        
    async def _run_linting(self, file_path: Path) -> List[str]:
        """Run ESLint on the code"""
        issues = []
        try:
            result = subprocess.run(
                ["npx", "eslint", str(file_path), "--format", "json"],
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                timeout=20
            )
            
            if result.stdout:
                lint_results = json.loads(result.stdout)
                for file_result in lint_results:
                    for message in file_result.get('messages', []):
                        if message['severity'] == 2:  # Error
                            issues.append(f"ESLint error: {message['message']} at line {message['line']}")
                            
        except Exception as e:
            logger.warning(f"Linting failed: {str(e)}")
            
        return issues
        
    async def _validate_backend(self, file_path: Path) -> List[str]:
        """Validate backend-specific code"""
        issues = []
        content = file_path.read_text()
        
        # Check for required imports and patterns
        if 'express' in content.lower() and 'import' not in content and 'require' not in content:
            issues.append("Backend code should import Express")
            
        if 'app.listen' not in content and 'server.listen' not in content:
            issues.append("Backend should include server startup code")
            
        return issues
        
    async def _validate_frontend(self, file_path: Path) -> List[str]:
        """Validate frontend-specific code"""
        issues = []
        content = file_path.read_text()
        
        # Check for React patterns
        if file_path.suffix == '.tsx' and 'import React' not in content:
            issues.append("React component should import React")
            
        return issues

class TaskPlanner:
    """Plans and prioritizes development tasks"""
    
    def __init__(self, vision: str, user_stories: List[str]):
        self.vision = vision
        self.user_stories = user_stories
        
    def create_tasks(self) -> List[GenerationTask]:
        """Create development tasks from user stories"""
        tasks = []
        
        # Core infrastructure tasks
        tasks.extend([
            GenerationTask(
                id="setup_project",
                type="setup",
                description="Create project structure and configuration files",
                dependencies=[],
                priority=1,
                estimated_tokens=2000
            ),
            GenerationTask(
                id="database_schema",
                type="database",
                description="Design and create database schema",
                dependencies=["setup_project"],
                priority=2,
                estimated_tokens=3000
            )
        ])
        
        # Backend tasks based on user stories
        for i, story in enumerate(self.user_stories):
            backend_task = GenerationTask(
                id=f"backend_story_{i}",
                type="backend",
                description=f"Implement backend for: {story}",
                dependencies=["database_schema"],
                priority=3,
                estimated_tokens=4000
            )
            tasks.append(backend_task)
            
            frontend_task = GenerationTask(
                id=f"frontend_story_{i}",
                type="frontend",
                description=f"Implement frontend for: {story}",
                dependencies=[f"backend_story_{i}"],
                priority=4,
                estimated_tokens=4000
            )
            tasks.append(frontend_task)
            
            test_task = GenerationTask(
                id=f"tests_story_{i}",
                type="tests",
                description=f"Create tests for: {story}",
                dependencies=[f"frontend_story_{i}"],
                priority=5,
                estimated_tokens=3000
            )
            tasks.append(test_task)
        
        # Deployment and final tasks
        tasks.extend([
            GenerationTask(
                id="deployment_config",
                type="deployment",
                description="Create deployment configuration for AWS/GCP",
                dependencies=[f"tests_story_{len(self.user_stories)-1}"],
                priority=6,
                estimated_tokens=3000
            ),
            GenerationTask(
                id="integration_tests",
                type="tests",
                description="Create end-to-end integration tests",
                dependencies=["deployment_config"],
                priority=7,
                estimated_tokens=4000
            )
        ])
        
        return tasks

class ApplicationGenerator:
    """Main orchestrator for application generation"""
    
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.budget_manager = BudgetManager(config.budget_dollars, config.cost_per_1k_tokens)
        self.ai_orchestrator = AIOrchestrator(config.api_key, self.budget_manager)
        self.code_validator = CodeValidator(config.output_dir)
        self.task_planner = TaskPlanner(config.vision, config.user_stories)
        self.output_dir = Path(config.output_dir)
        self.context = {
            'vision': config.vision,
            'user_stories': config.user_stories,
            'existing_structure': {}
        }
        
    async def generate_application(self):
        """Main method to generate the complete application"""
        logger.info(f"Starting application generation for: {self.config.project_name}")
        logger.info(f"Budget: ${self.config.budget_dollars}, Max runtime: {self.config.max_runtime_hours}h")
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create tasks
        tasks = self.task_planner.create_tasks()
        logger.info(f"Created {len(tasks)} development tasks")
        
        # Execute tasks
        start_time = datetime.now()
        max_end_time = start_time + timedelta(hours=self.config.max_runtime_hours)
        
        completed_tasks = []
        failed_tasks = []
        
        for task in sorted(tasks, key=lambda t: t.priority):
            if datetime.now() > max_end_time:
                logger.warning("Maximum runtime reached, stopping execution")
                break
                
            if not self.budget_manager.can_spend(task.estimated_tokens):
                logger.warning(f"Insufficient budget for task {task.id}, stopping execution")
                break
                
            try:
                await self._execute_task(task)
                completed_tasks.append(task)
                logger.info(f"Completed task: {task.id}")
                
                # Update context with completed task
                self._update_context(task)
                
            except Exception as e:
                logger.error(f"Task {task.id} failed: {str(e)}")
                failed_tasks.append(task)
                
                # Retry logic
                if task.retry_count < 2:
                    task.retry_count += 1
                    task.status = "pending"
                    tasks.append(task)  # Re-add for retry
                    
        # Generate final report
        await self._generate_final_report(completed_tasks, failed_tasks)
        
        logger.info(f"Generation complete. Completed: {len(completed_tasks)}, Failed: {len(failed_tasks)}")
        logger.info(f"Final budget spent: ${self.budget_manager.spent:.2f}")
        
    async def _execute_task(self, task: GenerationTask):
        """Execute a single development task"""
        task.status = "in_progress"
        
        # Generate code
        code_content, tokens_used = await self.ai_orchestrator.generate_code(task, self.context)
        
        # Extract files from the generated content
        files = self._extract_files_from_content(code_content, task)
        
        # Validate each file
        all_valid = True
        for file_path, content in files.items():
            is_valid, issues = await self.code_validator.validate_code(content, file_path, task.type)
            
            if not is_valid:
                logger.warning(f"Validation issues for {file_path}: {issues}")
                # Attempt to fix issues with another AI call if budget allows
                if self.budget_manager.can_spend(1000):  # Small budget for fixes
                    content = await self._fix_code_issues(content, issues, file_path)
                    
            # Write file to output directory
            output_path = self.output_dir / file_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content)
            
        task.status = "completed"
        
    def _extract_files_from_content(self, content: str, task: GenerationTask) -> Dict[str, str]:
        """Extract individual files from AI-generated content"""
        files = {}
        
        # Simple extraction based on common patterns
        # This is a simplified version - you might want to make it more sophisticated
        lines = content.split('\n')
        current_file = None
        current_content = []
        
        for line in lines:
            if line.startswith('// File:') or line.startswith('# File:'):
                if current_file and current_content:
                    files[current_file] = '\n'.join(current_content)
                current_file = line.split(':', 1)[1].strip()
                current_content = []
            elif line.startswith('```') and current_file:
                # Handle code blocks
                continue
            elif current_file:
                current_content.append(line)
                
        # Add the last file
        if current_file and current_content:
            files[current_file] = '\n'.join(current_content)
            
        # If no files were extracted, create a default file
        if not files:
            default_name = f"{task.type}_{task.id}.ts"
            files[default_name] = content
            
        return files
        
    async def _fix_code_issues(self, code: str, issues: List[str], file_path: str) -> str:
        """Attempt to fix code issues using AI"""
        fix_prompt = f"""
Fix the following issues in this code:

ISSUES:
{chr(10).join(f"- {issue}" for issue in issues)}

CODE:
{code}

Please provide the corrected code that addresses all the issues mentioned above.
Only return the fixed code, no explanations.
"""
        
        try:
            # Create a simple fix task
            fix_task = GenerationTask(
                id=f"fix_{file_path}",
                type="fix",
                description="Fix code issues",
                dependencies=[],
                priority=0,
                estimated_tokens=1000
            )
            
            fixed_content, _ = await self.ai_orchestrator.generate_code(fix_task, {'prompt': fix_prompt})
            return fixed_content
        except:
            return code  # Return original if fix fails
            
    def _update_context(self, completed_task: GenerationTask):
        """Update context with information from completed task"""
        if completed_task.type == "setup":
            self.context['existing_structure']['setup_complete'] = True
        elif completed_task.type == "database":
            self.context['existing_structure']['database_ready'] = True
        # Add more context updates as needed
            
    async def _generate_final_report(self, completed_tasks: List[GenerationTask], failed_tasks: List[GenerationTask]):
        """Generate a final report of the generation process"""
        report = {
            'project_name': self.config.project_name,
            'generation_date': datetime.now().isoformat(),
            'budget_used': self.budget_manager.spent,
            'budget_total': self.config.budget_dollars,
            'completed_tasks': len(completed_tasks),
            'failed_tasks': len(failed_tasks),
            'tasks_details': {
                'completed': [asdict(task) for task in completed_tasks],
                'failed': [asdict(task) for task in failed_tasks]
            },
            'spending_breakdown': self.budget_manager.transactions
        }
        
        # Write report
        report_path = self.output_dir / 'generation_report.json'
        report_path.write_text(json.dumps(report, indent=2))
        
        # Generate README
        readme_content = self._generate_readme()
        readme_path = self.output_dir / 'README.md'
        readme_path.write_text(readme_content)
        
    def _generate_readme(self) -> str:
        """Generate a comprehensive README for the project"""
        return f"""# {self.config.project_name}

## Project Vision
{self.config.vision}

## User Stories
{chr(10).join(f"- {story}" for story in self.config.user_stories)}

## Generated Application Structure

This application was automatically generated using AI orchestration. The following components were created:

### Backend
- RESTful API built with Express.js and TypeScript
- Database integration with PostgreSQL
- Input validation and error handling
- Health check endpoints

### Frontend
- React application with TypeScript
- Responsive design with Tailwind CSS
- State management with Redux Toolkit
- Proper routing and error boundaries

### Testing
- Unit tests with Jest
- Integration tests
- E2E tests with Playwright

### Deployment
- Docker configuration
- AWS/GCP deployment scripts
- CI/CD pipeline setup

## Getting Started

### Prerequisites
- Node.js 18+
- PostgreSQL
- Docker (optional)

### Installation

1. Install dependencies:
   ```bash
   npm install
   ```

2. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. Set up the database:
   ```bash
   npm run db:setup
   ```

4. Start the development server:
   ```bash
   npm run dev
   ```

### Deployment

Deploy to AWS:
```bash
npm run deploy:aws
```

Deploy to GCP:
```bash
npm run deploy:gcp
```

## Generated with AI
- Budget used: ${self.budget_manager.spent:.2f} USD
- Generation date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- Total development time: Automated

## Next Steps
1. Review the generated code
2. Customize the configuration
3. Add your specific business logic
4. Test thoroughly before deployment
"""

def load_config(config_path: str) -> ProjectConfig:
    """Load configuration from file"""
    with open(config_path, 'r') as f:
        config_data = yaml.safe_load(f)
        
    return ProjectConfig(
        budget_dollars=config_data['budget_dollars'],
        vision=config_data['vision'],
        user_stories=config_data['user_stories'],
        project_name=config_data['project_name'],
        output_dir=config_data['output_dir'],
        api_key=config_data['api_key'],
        max_runtime_hours=config_data.get('max_runtime_hours', 72),
        cost_per_1k_tokens=config_data.get('cost_per_1k_tokens', 0.003)
    )

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='AI-Powered Full Stack App Generator')
    parser.add_argument('--config', required=True, help='Path to configuration YAML file')
    args = parser.parse_args()
    
    try:
        config = load_config(args.config)
        generator = ApplicationGenerator(config)
        await generator.generate_application()
        
        print(f"\n🎉 Application generation complete!")
        print(f"📁 Output directory: {config.output_dir}")
        print(f"💰 Budget used: ${generator.budget_manager.spent:.2f} / ${config.budget_dollars}")
        print(f"📊 Check generation_report.json for detailed information")
        
    except Exception as e:
        logger.error(f"Generation failed: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
