import os
import yaml
from typing import Dict, Any, Optional


class YamlConfigLoader:
    """Handles loading and accessing YAML configuration files."""
    
    def __init__(self, 
                 config_path: Optional[str] = None) -> None:
        """
        Initialize the YAML config loader.
        
        Args:
            config_path: Path to the YAML config file. If None, uses default path.
        """
        if config_path is None:
            # Default path: project root/config/config.yaml
            # Navigate from app/utils/config_loader/ to project root
            current_dir = os.path.dirname(__file__)
            project_root = os.path.abspath(os.path.join(current_dir, "../../../"))
            config_path = os.path.join(project_root, "config", "config.yaml")
        
        self.config_path = config_path
        self._config = None
        self._load_config()
    
    def _load_config(self) -> None:
        """Load the YAML configuration file."""
        try:
            if not os.path.exists(self.config_path):
                raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
            
            with open(self.config_path, 'r', encoding='utf-8') as file:
                self._config = yaml.safe_load(file)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML syntax in {self.config_path}: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load configuration from {self.config_path}: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.
        
        Args:
            key: Configuration key (supports dot notation for nested keys)
            default: Default value if key is not found
            
        Returns:
            Configuration value or default
        """
        if self._config is None:
            return default
        
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get an entire configuration section.
        
        Args:
            section: Section name
            
        Returns:
            Dictionary containing the section configuration
        """
        return self.get(section, {})


# Global instance for easy access
_yaml_loader = YamlConfigLoader()


def get_yaml_config() -> YamlConfigLoader:
    """Get the global YAML config loader instance."""
    return _yaml_loader
