import ConfigParser
import re
import logging
import pkg_resources

from yandextank.common.util import recursive_dict_update
from yandextank.validator.validator import load_plugin_schema, load_yaml_schema

logger = logging.getLogger(__name__)


def old_plugin_mapper(package):
    MAP = {'Overload': 'DataUploader'}
    return MAP.get(package, package)


def parse_package_name(package_path):
    if package_path.startswith("Tank/Plugins/"):
        package = package_path.split('/')[-1].split('.')[0]
    else:
        package = package_path.split('.')[-1]
    return old_plugin_mapper(package)


SECTIONS_PATTERNS = {
    'Aggregator': 'aggregator',
    'Android': 'android',
    'Appium': 'appium',
    'Autostop': 'autostop',
    'BatteryHistorian': 'battery_historian',
    'Bfg': 'bfg|ultimate_gun|http_gun|custom_gun|scenario_gun',
    'Phantom': 'phantom(-.*)?',
    'DataUploader': 'meta|overload',
    'Telegraf': 'telegraf|monitoring',
    'JMeter': 'jmeter',
    'ResourceCheck': 'rcheck',
    'ShellExec': 'shellexec',
    'Console': 'console',
    'TipsAndTricks': 'tips',
    'RCAssert': 'rcassert'
}


class UnrecognizedSection(Exception):
    pass


def guess_plugin(section):
    for plugin, section_name_pattern in SECTIONS_PATTERNS.items():
        if re.match(section_name_pattern, section):
            return plugin
    else:
        raise UnrecognizedSection('Section {} did not match any plugin'.format(section))


def convert_rps_schedule(key, value):
    return {'load_profile': {
        'load_type': 'rps',
        'schedule': value
    }}


def convert_instances_schedule(key, value):
    return {'load_profile': {
        'load_type': 'instances',
        'schedule': value
    }}


def to_bool(value):
    try:
        return bool(int(value))
    except ValueError:
        return True if 'true' == value.lower() else False


OPTIONS_MAP = {
    'Phantom': {
        'rps_schedule': convert_rps_schedule,
        'instances_schedule': convert_instances_schedule,
    },
    'DataUploader': {}
}


def type_cast(plugin, option, value, schema=None):
    type_map = {
        'boolean': to_bool,
        'integer': int,
    }
    schema = schema if schema else load_plugin_schema('yandextank.plugins.' + plugin)

    if schema.get(option) is None:
        logger.warning('Unknown option {}:{}'.format(plugin, option))
        return value

    _type = schema[option].get('type', None)
    if _type is None:
        logger.warning('Option {}:{}: no type specified in schema'.format(plugin, option))
        return value
    return type_map.get(_type, lambda x: x)(value)


def option_converter(plugin, option, schema=None):
    # type: (str, (str, str), dict) -> (str, str)
    key, value = option
    return OPTIONS_MAP.get(plugin, {}).get(key, lambda k, v: (key, type_cast(plugin, key, value, schema)))(key, value)


def is_option_deprecated(plugin, option_name):
    DEPRECATED = {
        'Aggregator': [
            'time_periods',
            'precise_cumulative'
        ],
        'Phantom': [
            'stpd_file'
        ]
    }
    if option_name in DEPRECATED.get(plugin, []):
        logger.warning('Deprecated option {} in plugin {}, omitting'.format(option_name, plugin))
        return True
    else:
        return False


def without_deprecated(plugin, options):
    """
    :type options: list of tuple
    """
    return filter(lambda option: not is_option_deprecated(plugin, option[0]), options)


def old_section_name_mapper(name):
    MAP = {
        'monitoring': 'telegraf',
        'meta': 'uploader'
    }
    return MAP.get(name, name)


class UnknownOption(Exception):
    pass


class Option(object):
    SPECIAL_CONVERTERS = {
        'Phantom': {
            'rps_schedule': convert_rps_schedule,
            'instances_schedule': convert_instances_schedule,
        },
        'Bfg': {
            'rps_schedule': convert_rps_schedule,
            'instances_schedule': convert_instances_schedule,
        },
    }
    CONVERTERS_FOR_UNKNOWN = {
        'DataUploader': lambda k, v: {'meta': {k: v}}
    }

    def __init__(self, plugin, key, value, schema=None):
        self.plugin = plugin
        self.name = key
        self.value = value
        self.schema = schema
        self.dummy_converter = lambda k, v: {k: v}
        self._converted = None
        self._converter = None

    @property
    def converted(self):
        if self._converted is None:
            self._converted = self.converter(self.name, self.value)
        return self._converted

    @property
    def converter(self):
        if self._converter is None:
            try:
                return self.SPECIAL_CONVERTERS[self.plugin][self.name]
            except KeyError:
                try:
                    return self._get_scheme_converter()
                except UnknownOption:
                    return self.CONVERTERS_FOR_UNKNOWN.get(self.plugin, self.dummy_converter)

    def _get_scheme_converter(self):
        type_map = {
            'boolean': lambda k, v: {k: to_bool(v)},
            'integer': lambda k, v: {k: int(v)},
        }
        schema = self.schema if self.schema else load_plugin_schema('yandextank.plugins.' + self.plugin)

        if schema.get(self.name) is None:
            logger.warning('Unknown option {}:{}'.format(self.plugin, self.name))
            raise UnknownOption

        _type = schema[self.name].get('type', None)
        if _type is None:
            logger.warning('Option {}:{}: no type specified in schema'.format(self.plugin, self.name))
            return self.dummy_converter

        return type_map.get(_type, self.dummy_converter)


class Section(object):
    def __init__(self, name, plugin, options, enabled=None):
        self.init_name = name
        self.name = old_section_name_mapper(name)
        self.plugin = plugin
        self.schema = load_plugin_schema('yandextank.plugins.' + plugin)
        self.options = [Option(plugin, *option, schema=self.schema) for option in without_deprecated(plugin, options)]
        self.enabled = enabled
        self._merged_options = None

    def get_cfg_dict(self, with_meta=True):
        options_dict = self.merged_options
        if with_meta:
            options_dict.update({'package': 'yandextank.plugins.{}'.format(self.plugin)})
            if self.enabled is not None:
                options_dict.update({'enabled': self.enabled})
        return options_dict

    @property
    def merged_options(self):
        if self._merged_options is None:
            self._merged_options = reduce(lambda acc, upd: recursive_dict_update(acc, upd),
                                          [opt.converted for opt in self.options],
                                          {})
        return self._merged_options

    @classmethod
    def from_multiple(cls, sections, parent_name=None, child_name=None, is_list=True):
        """
        :type parent_name: str
        :type sections: list of Section
        """
        if len(sections) == 1:
            return sections[0]
        if parent_name:
            master_section = filter(lambda section: section.name == parent_name, sections)[0]
            rest = filter(lambda section: section.name != parent_name, sections)
        else:
            master_section = sections[0]
            parent_name = master_section.name
            rest = sections[1:]
        child = {'multi': [section.get_cfg_dict(with_meta=False) for section in rest]} if is_list \
            else {child_name: rest[0].get_cfg_dict(with_meta=False)}
        master_section.merged_options.update(child)
        return master_section


def without_defaults(cfg_ini, section):
    """

    :type cfg_ini: ConfigParser.ConfigParser
    """
    defaults = cfg_ini.defaults()
    options = cfg_ini.items(section) if cfg_ini.has_section(section) else []
    return [(key, value) for key, value in options if key not in defaults.keys()]


PLUGIN_PREFIX = 'plugin_'
CORE_SECTION = 'tank'


def parse_sections(cfg_ini):
    """
    :type cfg_ini: ConfigParser.ConfigParser
    """
    return [Section(section,
                    guess_plugin(section),
                    without_defaults(cfg_ini, section))
            for section in cfg_ini.sections()
            if section != CORE_SECTION]


def enable_sections(sections, core_options):
    """

    :type sections: list of Section
    """
    enabled_plugins = [parse_package_name(value) for key, value in core_options if
                       key.startswith(PLUGIN_PREFIX) and value]
    disabled_plugins = [guess_plugin(key.split('_')[1]) for key, value in core_options if
                        key.startswith(PLUGIN_PREFIX) and not value]
    for section in sections:
        if section.plugin in enabled_plugins:
            section.enabled = True
            enabled_plugins.remove(section.plugin)
        if section.plugin in disabled_plugins:
            section.enabled = False
            disabled_plugins.remove(section.plugin)
    for plugin in enabled_plugins:
        sections.append(Section(plugin.lower(), plugin, [], True))
    for plugin in disabled_plugins:
        sections.append(Section(plugin.lower(), plugin, [], False))
    return sections


def partition(l, predicate):
    return reduce(lambda x, y: (x[0] + [y], x[1]) if predicate(y) else (x[0], x[1] + [y]), l, ([], []))


def combine_sections(sections):
    """
    :type sections: list of Section
    :rtype: list of Section
    """
    PLUGINS_TO_COMBINE = {
        'Phantom': ('phantom', 'multi', True),
        'Bfg': ('bfg', 'gun_config', False)
    }
    plugins = {}
    for section in sections:
        if section.plugin in PLUGINS_TO_COMBINE.keys():
            try:
                plugins[section.plugin].append(section)
            except KeyError:
                plugins[section.plugin] = [section]
        else:
            plugins[section.plugin] = section

    for plugin_name, _sections in plugins.items():
        if isinstance(_sections, list):
            parent_name, child_name, is_list = PLUGINS_TO_COMBINE[plugin_name]
            plugins[plugin_name] = Section.from_multiple(_sections, parent_name, child_name, is_list)
    return plugins.values()


def core_options(cfg_ini):
    return cfg_ini.items(CORE_SECTION) if cfg_ini.has_section(CORE_SECTION) else []


def convert_ini(ini_file):
    cfg_ini = ConfigParser.ConfigParser()
    cfg_ini.read(ini_file)
    ready_sections = enable_sections(combine_sections(parse_sections(cfg_ini)), core_options(cfg_ini))

    plugins_cfg_dict = {section.name: section.get_cfg_dict() for section in ready_sections}
    core_opts_schema = \
        load_yaml_schema(pkg_resources.resource_filename('yandextank.core', 'config/schema.yaml'))['core']['schema']

    plugins_cfg_dict.update({
        'core': dict(
            [option_converter('core', option, core_opts_schema) for option in without_defaults(cfg_ini, CORE_SECTION)
             if not option[0].startswith(PLUGIN_PREFIX)])
    })
    return plugins_cfg_dict
