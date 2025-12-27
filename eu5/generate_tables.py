from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import os
from operator import attrgetter

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from common.paradox_lib import unsorted_groupby
from common.paradox_parser import Tree
from eu5.eu5_file_generator import Eu5FileGenerator
from eu5.eu5lib import GoodCategory, Eu5GameConcept, Building, Law, LawPolicy, Good, EstatePrivilege
from eu5.parser import Eu5Parser


class TableGenerator(Eu5FileGenerator):

    def format_modifier_section(self, section: str, entity):
        if hasattr(entity, section):
            return self.create_wiki_list([modifier.format_for_wiki() for modifier in getattr(entity, section)])
        else:
            return ''

    def merge_multiple_sections(self, headers_with_content: dict[str, str]|list[tuple[str, str]]):
        result = []
        if isinstance(headers_with_content, dict):
            headers_with_content = [(header, content) for header, content in headers_with_content.items()]
        for header, content in headers_with_content:
            if content.strip() == '':
                continue
            section = []
            if header:
                section.append(f"'''{header}:'''")
            section.append(content)
            result.append('\n'.join(section))
        return '\n'.join(result)

    def infer_country_rank_key(self, country) -> str | None:
        """Infer a reasonable country_rank when not explicitly set.

        More conservative heuristic using unique provinces controlled:
        - <= 2 provinces -> rank_county
        - <= 5 provinces -> rank_duchy
        - <= 12 provinces -> rank_kingdom
        - > 12 provinces -> rank_empire

        Returns a rank key like 'rank_county' or None if insufficient data.
        """
        candidates = [
            'own_control_core', 'own_core', 'control',
            'own_conquered', 'own_control_conquered', 'own_control_integrated', 'own_control_colony',
        ]
        provinces = set()
        for attr in candidates:
            if hasattr(country, attr):
                value = getattr(country, attr)
                if isinstance(value, list) and len(value) > 0:
                    for loc in value:
                        # value entries are Location objects due to parser annotations
                        try:
                            prov = loc.province
                        except Exception:
                            prov = None
                        if prov is not None:
                            provinces.add(prov)
        n = len(provinces)
        if n <= 0:
            return 'rank_county'  # default to smallest when uncertain
        if n <= 2:
            return 'rank_county'
        if n <= 5:
            return 'rank_duchy'
        if n <= 12:
            return 'rank_kingdom'
        return 'rank_empire'

    def get_building_notes(self, building: Building):
        result = []
        messages_for_non_default_values = {
            'always_add_demands': 'Demand does not scale with workers',
            'AI_ignore_available_worker_flag': 'Build by AI even without available workers',
            'AI_optimization_flag_coastal': '',
            'allow_wrong_startup': '<tt>allow_wrong_startup</tt>',
            'can_close': 'Cannot be closed',
            'conversion_religion': f'Converts pops to {building.conversion_religion}',
            'forbidden_for_estates': 'Cannot be build by estates',
            'increase_per_level_cost': f'Cost changes by {self.formatter.add_red_green(building.increase_per_level_cost, positive_is_good=False, add_plus=True, add_percent=True)} per level',
            'in_empty': f'Can { {"empty": "only", "any": "also", "owned": "not"}[building.in_empty] } be built in empty locations',
            'is_foreign': 'Foreign building',
            'lifts_fog_of_war': 'Lifts fog of war',
            'need_good_relation': 'Needs good relations when building in foreign provinces',
            'pop_size_created': f'Creates {building.pop_size_created} pops when building(taken from the capital of the owner)',
            'stronger_power_projection': 'Requires more power projection to construct in a foreign location',
            'on_built': "'''On Built:'''\n" + self.formatter.format_effect(building.on_built),
            'on_destroyed': "'''On Destroyed:'''\n" + self.formatter.format_effect(building.on_destroyed),
        }
        for attribute, message in messages_for_non_default_values.items():
            if getattr(building, attribute) != building.default_values[attribute]:
                result.append(message)

        return self.create_wiki_list(result)

    def format_pms(self, building):
        pm_lists = building.unique_production_methods.copy()
        if building.possible_production_methods:
            pm_lists.append(building.possible_production_methods)
        formatted_pm_categories = []
        for pm_list in pm_lists:
            formatted_pms = []
            for pm in pm_list:
                formatted_pms.extend(pm.format(icon_only=True))
            formatted_pm_categories.append(self.create_wiki_list(formatted_pms))
        return '\n----\n'.join(formatted_pm_categories)

    def generate_building_tables(self):
        result = []
        previous_type = None
        for (type_name, category), table in self.get_building_tables().items():
            if type_name != previous_type:
                result.append(f'== {type_name} buildings ==')
                previous_type = type_name
            result.append(f'=== {category.display_name} ===')

            section_content = f'{{{{iconbox||{category.description}|image={category.get_wiki_filename()}}}}}' + '\n' + table
            result.append(self.surround_with_autogenerated_section(f'buildings_{type_name.replace("+", "_")}_{category.name}', section_content, add_version_header=True))
        return result

    def get_building_sections(self):
        return {
            f'buildings_{type_name.replace("+", "_")}_{category.name}': f'{{{{iconbox||{category.description}|image={category.get_wiki_filename()}}}}}' + '\n' + table
            for (type_name, category), table in self.get_building_tables().items()
        }

    def get_building_tables(self):
        results = {}
        type_names = {(True, True, True): 'common',
                          (False, False, True): 'rural',
                          (False, True, False): 'town',
                          (True, False, False): 'city',
                          (True, True, False): 'town+city',
                          (False, True, True): 'town+rural',
                          (True, False, True): 'city+rural',
                          (False, False, False): 'nowhere',
                          }
        buildings_by_location_type = {type_names[typ]: list(buildings) for typ, buildings in unsorted_groupby(self.parser.buildings.values(), key=attrgetter('city', 'town', 'rural_settlement'))}
        for type_name, buildings_for_type in buildings_by_location_type.items():
            buildings_by_category = unsorted_groupby(buildings_for_type, key=attrgetter('category'))
            for category, buildings in buildings_by_category:
                sorted_buildings = sorted(buildings, key=attrgetter('display_name'))
                results[(type_name, category)] = self.get_building_table(sorted_buildings)
        return results

    def get_building_table(self, sorted_buildings: list[Building]):
        # sorted_buildings = [b for b in sorted_buildings if b.possible_production_methods and not isinstance(b.possible_production_methods[0], ProductionMethod)]
        # sorted_buildings = [self.parser.buildings['jewelry_guild']] + sorted_buildings[:20]
        buildings = [{
            # 'Name': f'{{{{iconbox|{building.display_name}|{building.description}|w=300px|image={building.get_wiki_filename()}}}}}',
            # 'Time': building.build_time,
            # 'Price': building.price.format() if isinstance(building.price, Price) else building.price,
            # 'Destroy Price': building.destroy_price.format() if building.destroy_price else '',
            # 'Construction demand': building.construction_demand.format(icon_only=True) if hasattr(building.construction_demand, 'format') else building.construction_demand,
            # 'category': building.category,
            # 'foreign':  building.is_foreign,
            # 'Pop': building.pop_type,
            # 'Employees': round(building.employment_size),
            # 'Town': building.town,
            # 'City': building.city,
            # 'Max levels': building.max_levels,
            # 'Modifiers': self.format_modifier_section('modifier', building),
            # 'Modifiers if in capital': self.format_modifier_section('capital_modifier', building),
            # 'Country modifiers if in capital': self.format_modifier_section('capital_country_modifier', building),
            'Name': f'{{{{iconbox|{building.display_name}|{building.description}|w=300px|desc_class=hidem|image={building.get_wiki_filename()}}}}}',
'Modifier': self.format_modifier_section('modifier', building),  # modifier: list[eu5.eu5lib.Eu5Modifier]
'Requirements': self.merge_multiple_sections([
    ('Location', self.formatter.format_trigger(building.location_potential) + self.formatter.format_trigger(building.allow)),
    ('Country', self.formatter.format_trigger(building.country_potential)),
    ('To destroy', self.formatter.format_trigger(building.can_destroy)),
    ('To keep', self.formatter.format_trigger(building.remove_if))
]),
# 'Country Potential': self.formatter.format_trigger(building.country_potential),  # country_potential: <class 'eu5.eu5lib.Trigger'>
# 'Location Potential': self.formatter.format_trigger(building.location_potential),  # location_potential: <class 'eu5.eu5lib.Trigger'>
# 'Allow': self.formatter.format_trigger(building.allow),  # allow: <class 'eu5.eu5lib.Trigger'>
# 'Can Destroy': self.formatter.format_trigger(building.can_destroy),  # can_destroy: <class 'eu5.eu5lib.Trigger'>
# 'Remove If': self.formatter.format_trigger(building.remove_if),  # remove_if: <class 'eu5.eu5lib.Trigger'>

'Build Time': building.build_time,  # build_time: <class 'int'>
'Modifiers': self.merge_multiple_sections([
    # 'Capital Country Modifier': self.format_modifier_section('capital_country_modifier', building),  # capital_country_modifier: list[eu5.eu5lib.Eu5Modifier]
    ('Country (if in Capital)', self.format_modifier_section('capital_country_modifier', building)),  # capital_country_modifier: list[eu5.eu5lib.Eu5Modifier]
# 'Capital Modifier': self.format_modifier_section('capital_modifier', building),  # capital_modifier: list[eu5.eu5lib.Eu5Modifier]
    ('Location (if in Capital)', self.format_modifier_section('capital_modifier', building)),
# 'Market Center Modifier': self.format_modifier_section('market_center_modifier', building),  # market_center_modifier: list[eu5.eu5lib.Eu5Modifier]
    ('Location (if in Market Center)', self.format_modifier_section('market_center_modifier', building)),
# 'Foreign Country Modifier': self.format_modifier_section('foreign_country_modifier', building),  # foreign_country_modifier: list[eu5.eu5lib.Eu5Modifier]
    ('Building owner', self.format_modifier_section('foreign_country_modifier', building)),
    # 'Raw Modifier': self.format_modifier_section('raw_modifier', building),  # raw_modifier: list[eu5.eu5lib.Eu5Modifier]
    ('Raw Modifier', self.format_modifier_section('raw_modifier', building)),
]),
# 'Category': building.category,  # category: <class 'str'>
# 'City': '[[File:Yes.png|20px|City]]' if building.city else '[[File:No.png|20px|Not City]]',  # city: <class 'bool'>
'Construction Demand': building.construction_demand.format(icon_only=True) if hasattr(building.construction_demand, 'format') else building.construction_demand,  # construction_demand: <class 'eu5.eu5lib.GoodsDemand'>
'Destroy Price': building.destroy_price.format(icon_only=True) if hasattr(building.destroy_price, 'format') else building.destroy_price,  # destroy_price: <class 'eu5.eu5lib.Price'>
'Employment': f'{building.employment_size:g} {building.pop_type.get_wiki_icon()}',  # employment_size: <class 'float'>
'Estate': building.estate.get_wiki_link_with_icon() if building.estate else '',
# 'Graphical Tags': self.create_wiki_list([graphical_tags for graphical_tags in building.graphical_tags]),  # graphical_tags: list[str]
'Max Levels': building.max_levels,  # max_levels: int | str
'Obsolete': self.create_wiki_list([obsolete.get_wiki_link_with_icon() if obsolete else '' for obsolete in building.obsolete]),  # obsolete: list[eu5.eu5lib.Building]
# 'On Built': self.formatter.format_effect(building.on_built),  # on_built: <class 'eu5.eu5lib.Effect'>
# 'On Destroyed': self.formatter.format_effect(building.on_destroyed),  # on_destroyed: <class 'eu5.eu5lib.Effect'>
'Production Methods': self.format_pms(building),  # possible_production_methods: list[eu5.eu5lib.ProductionMethod]
# 'Possible Production Methods': self.create_wiki_list([possible_production_methods.format(icon_only=True) if hasattr(possible_production_methods, 'format') else possible_production_methods for possible_production_methods in building.possible_production_methods]),  # possible_production_methods: list[eu5.eu5lib.ProductionMethod]
# 'Unique Production Methods': self.create_wiki_list([unique_production_methods.format(icon_only=True) if hasattr(unique_production_methods, 'format') else unique_production_methods for unique_production_methods in building.unique_production_methods]),  # unique_production_methods: list[eu5.eu5lib.ProductionMethod]
'Price': building.price.format(icon_only=True) if hasattr(building.price, 'format') else building.price,  # price: <class 'eu5.eu5lib.Price'>


# 'Rural Settlement': '[[File:Yes.png|20px|Rural Settlement]]' if building.rural_settlement else '[[File:No.png|20px|Not Rural Settlement]]',  # rural_settlement: <class 'bool'>
# 'Town': '[[File:Yes.png|20px|Town]]' if building.town else '[[File:No.png|20px|Not Town]]',  # town: <class 'bool'>
        'Notes': self.get_building_notes(building),
        } for building in sorted_buildings]
        return self.make_wiki_table(buildings, table_classes=['mildtable', 'plainlist'],
                                     one_line_per_cell=True,
                                     remove_empty_columns=True,
                                     )
    def create_cargo_tenplate_calls(self, data: list[dict[str, Any]], template_name: str):
        lines = []
        for item_data in data:
            lines.append(f'=== {item_data["display_name"]} ===')
            lines.append(f'{{{{{template_name}')
            for column, value in item_data.items():
                lines.append(f'|{column}={value}')
            lines.append('}}')
        return '\n'.join(lines)

    def generate_building_table_cargo(self):
        sorted_buildings = sorted(
            self.parser.buildings.values(),
            #[good for good in self.parser.goods.values() if good.category == category and good.method == method]
            key=attrgetter('display_name')
            )
        buildings = [{
            'name': building.name,
            'display_name': building.display_name,
            'description': building.description,
            'icon': building.get_wiki_filename(),
            'modifier': self.format_modifier_section('modifier', building),  # modifier: list[eu5.eu5lib.Eu5Modifier]
            'allow': self.formatter.format_trigger(building.allow),  # allow: <class 'eu5.eu5lib.Trigger'>
            'build_time': building.build_time,  # build_time: <class 'int'>
            'can_destroy': self.formatter.format_trigger(building.can_destroy),  # can_destroy: <class 'eu5.eu5lib.Trigger'>
            'capital_country_modifier': self.format_modifier_section('capital_country_modifier', building),
            # capital_country_modifier: list[eu5.eu5lib.Eu5Modifier]
            'capital_modifier': self.format_modifier_section('capital_modifier', building),  # capital_modifier: list[eu5.eu5lib.Eu5Modifier]
            'category': building.category.name,  # category: <class 'str'>
            'city': 1 if building.city else 0,  # city: <class 'bool'>
            'construction_demand': building.construction_demand.format(icon_only=True) if hasattr(building.construction_demand,
                                                                                                  'format') else building.construction_demand,
            # construction_demand: <class 'eu5.eu5lib.GoodsDemand'>
            'country_potential': self.formatter.format_trigger(building.country_potential),  # country_potential: <class 'eu5.eu5lib.Trigger'>
            'destroy_price': building.destroy_price.format(icon_only=True) if hasattr(building.destroy_price, 'format') else building.destroy_price,
            # destroy_price: <class 'eu5.eu5lib.Price'>
            'employment_size': building.employment_size,  # employment_size: <class 'float'>
            'estate': building.estate.name if building.estate else '',
            'foreign_country_modifier': self.format_modifier_section('foreign_country_modifier', building),
            # foreign_country_modifier: list[eu5.eu5lib.Eu5Modifier]
            'graphical_tags': ';'.join([graphical_tags for graphical_tags in building.graphical_tags]),  # graphical_tags: list[str]
            'location_potential': self.formatter.format_trigger(building.location_potential),  # location_potential: <class 'eu5.eu5lib.Trigger'>
            'market_center_modifier': self.format_modifier_section('market_center_modifier', building),  # market_center_modifier: list[eu5.eu5lib.Eu5Modifier]
            'max_levels': building.max_levels,  # max_levels: int | str
            'obsolete': ';'.join([obsolete.name if obsolete else '' for obsolete in building.obsolete]),  # obsolete: list[eu5.eu5lib.Building]
            'on_built': self.formatter.format_effect(building.on_built),  # on_built: <class 'eu5.eu5lib.Effect'>
            'on_destroyed': self.formatter.format_effect(building.on_destroyed),  # on_destroyed: <class 'eu5.eu5lib.Effect'>
            'pop_type': building.pop_type.name if building.pop_type else '',
            'possible_production_methods': self.create_wiki_list(
                [pm.format(icon_only=True) for pm in building.possible_production_methods]),  # possible_production_methods: list[eu5.eu5lib.ProductionMethod]
            'price': building.price.format(icon_only=True) if hasattr(building.price, 'format') else building.price,  # price: <class 'eu5.eu5lib.Price'>
            'raw_modifier': self.format_modifier_section('raw_modifier', building),  # raw_modifier: list[eu5.eu5lib.Eu5Modifier]
            'remove_if': self.formatter.format_trigger(building.remove_if),  # remove_if: <class 'eu5.eu5lib.Trigger'>
            'rural_settlement': 1 if building.rural_settlement else 0,  # rural_settlement: <class 'bool'>
            'town': 1 if building.town else 0,  # town: <class 'bool'>
            'unique_production_methods': ';'.join([self.create_wiki_list(
                [pm.format(icon_only=True) for pm in pms]) for pms in building.unique_production_methods]),
            # unique_production_methods: list[list[eu5.eu5lib.ProductionMethod]]
            'notes': self.get_building_notes(building),
        } for building in sorted_buildings]
        return self.create_cargo_tenplate_calls(buildings, 'Building')

    def generate_building_categories_cargo(self):
        sorted_categories = sorted(
            self.parser.building_category.values(),
            key=attrgetter('display_name')
            )
        categories = [{
            'name': category.name,
            'display_name': category.display_name,
            'description': category.description,
            'icon': category.get_wiki_filename(),
        } for category in sorted_categories]
        return self.create_cargo_tenplate_calls(categories, 'Building_category')

    def generate_concept_tables(self):
        concepts = sorted(self.parser.game_concepts.values(), key=attrgetter('family', 'display_name'))
        result = []
        family = None
        all_concept_names = list(self.parser.game_concepts.keys()) + [concept.display_name for concept in concepts]
        for concept in concepts:
            if concept.is_alias:
                continue
            if concept.family != family:
                family = concept.family
                result.append(f'== {family if family else "Uncategorized"} ==')
            result.extend(self.get_concept_section(all_concept_names, concept))
        return result

    def get_concept_section(self, all_concept_names, concept: Eu5GameConcept):
        result = [f'=== {concept} ===']
        if concept.alias:
            alias_display_names = [alias.display_name for alias in concept.alias]
            result.append(
                f'{{{{hatnote|Aliases: {", ".join(self.formatter.format_localization_text(alias_display_name, all_concept_names) for alias_display_name in alias_display_names)}}}}}{{{{anchor|{"}}{{anchor|".join(alias_display_names)}}}}}')
        result.append(f'<section begin=autogenerated_concept_{concept.name}/>{self.get_concept_section_contents(all_concept_names, concept)}<section end=autogenerated_concept_{concept.name}/>')
        return result

    def get_concept_section_contents(self, all_concept_names, concept):
        return self.formatter.format_localization_text(concept.description, all_concept_names)

    def generate_concept_tables_russian(self):
        concepts = sorted(self.parser.game_concepts.values(), key=attrgetter('family', 'display_name'))
        ru_parser = Eu5Parser(language='russian')
        result = []
        family = None
        all_concept_names = list(self.parser.game_concepts.keys()) + [concept.display_name for concept in concepts] + [concept.display_name for concept in ru_parser.game_concepts.values()]
        table_data = []
        for concept in concepts:
            if concept.is_alias:
                continue
            ru_concept = ru_parser.game_concepts[concept.name]
            if concept.family != family:
                if table_data:
                    result.append(self.make_wiki_table(table_data, table_classes=['mildtable', 'plainlist'],
                                                       one_line_per_cell=True,
                                                       remove_empty_columns=True,
                                                       ))
                    table_data = []
                family = concept.family
                result.append(f'== {family if family else "Uncategorized"} ==')
            table_data.append(self.get_concept_section_russian(all_concept_names, concept, ru_concept, ru_parser))
        result.append(self.make_wiki_table(table_data, table_classes=['mildtable', 'plainlist'],
                                           one_line_per_cell=True,
                                           remove_empty_columns=True,
                                           ))
        return result

    def get_concept_section_russian(self, all_concept_names, concept: Eu5GameConcept, ru_concept: Eu5GameConcept, ru_parser: Eu5Parser):
        result = [f'=== {concept} ===']

        if concept.alias:
            alias_display_names = [alias.display_name for alias in concept.alias]
            ru_alias_display_names = [alias.display_name for alias in ru_concept.alias]
            aliases = f'{", ".join(self.formatter.format_localization_text(alias_display_name, all_concept_names) for alias_display_name in alias_display_names)}{{{{anchor|{"}}{{anchor|".join(alias_display_names)}}}}}'
            ru_aliases = f'{", ".join(self.formatter.format_localization_text(alias_display_name, all_concept_names) for alias_display_name in ru_alias_display_names)}{{{{anchor|{"}}{{anchor|".join(ru_alias_display_names)}}}}}'
        else:
            aliases = ''
            ru_aliases = ''

        return {
            'Concept': concept.display_name,
            'Aliases': aliases,
            'Text': self.get_concept_section_contents(all_concept_names, concept),
            'RU Concept': ru_concept.display_name,
            'RU Aliases': ru_aliases,
            'RU Text': f'<section begin=autogenerated_concept_{concept.name}/>{self.get_concept_section_contents(all_concept_names, ru_concept)}<section end=autogenerated_concept_{concept.name}/>',
        }

    def generate_estate_privileges_table(self):
        result = []
        for sectionname, section in self.get_privileges_sections().items():
            estate = sectionname.removeprefix('estate_privileges_')
            result.append(f'=== {self.parser.estates[estate].display_name} privileges ===')
            result.append(self.surround_with_autogenerated_section(sectionname, section, add_version_header=True))
        return result

    def get_privileges_sections(self):
        sections = {}
        for estate, privileges in unsorted_groupby(self.parser.estate_privileges.values(), key=attrgetter('estate')):
            privileges = sorted(privileges, key=attrgetter('display_name'))
            sections[f'estate_privileges_{estate.name}'] = self.get_privileges_table(privileges)
        return sections

    def get_privileges_table(self, privileges: list[EstatePrivilege]):
        privilege_table_data = [{
            'Name': f'{{{{iconbox|{privilege.display_name}|{privilege.description}|w=300px|image={privilege.get_wiki_filename()}}}}}',
            # 'Estate': privilege.estate.get_wiki_link_with_icon() if privilege.estate else '',
            'Requirements': self.merge_multiple_sections([
                ('', self.formatter.format_trigger(privilege.potential)),
                ('', self.formatter.format_trigger(privilege.allow)),
                ('Can Revoke', self.formatter.format_trigger(privilege.can_revoke)),
            ]),
            'On Fully Activated': self.formatter.format_effect(privilege.on_fully_activated),
            'Effects': self.merge_multiple_sections([
                ('', self.format_modifier_section('country_modifier', privilege)),
                ('On Activate', self.formatter.format_effect(privilege.on_activate)),
                ('On Deactivate', self.formatter.format_effect(privilege.on_deactivate)),
                ('Location Modifier', self.format_modifier_section('location_modifier', privilege)),
                ('Province Modifier', self.format_modifier_section('province_modifier', privilege)),
            ])
        } for privilege in privileges]
        return self.make_wiki_table(privilege_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )

    def generate_goods_tables(self):
        result = []
        for section, table in self.get_goods_tables().items():
            if not table:
                continue
            if section.endswith('_food'):
                result.append(f'==== Food ====')
            else:
                result.append(f'=== {self.localize(section).title()} ===')

            result.append(self.surround_with_autogenerated_section(section, table, add_version_header=True))
        return result

    def get_goods_tables(self):
        result = {}
        for category in GoodCategory:
            goods_in_category = [good for good in self.parser.goods.values() if good.category == category]
            special_goods = []
            if category == GoodCategory.produced:
                goods_in_category_without_method = []
                for good in goods_in_category:
                    special_goods.append(good) if good.method else goods_in_category_without_method.append(good)
                goods_in_category = goods_in_category_without_method
            result[f'{category}'] = self.get_goods_table([good for good in goods_in_category if good.food == 0])
            result[f'{category}_food'] = self.get_goods_table([good for good in goods_in_category if good.food > 0])
            result[f'{category}_special'] = self.get_goods_table(special_goods)

        return result

    def _get_goods_iconbox(self, good) -> str:
        lightness = good.color.rgb_r + good.color.rgb_g + good.color.rgb_b
        if lightness > 2:
            shadow = 'text-shadow:1px 1px 3px #000000'
        else:
            shadow = 'text-shadow:1px 1px 3px'
        shadow = f';{shadow}'
        return f'{{{{iconbox|{good.display_name}|{good.description}|w=300px|desc_class=hidem|image={good.get_wiki_filename()}|color={good.color.css_color_string}{shadow}}}}}'

    def get_goods_table(self, goods: Iterable[Good]):
        sorted_goods = sorted(goods, key=attrgetter('display_name'))

        if not sorted_goods:
            return ''
        goods = []
        for good in sorted_goods:
            row = {
                'rowspan="2" | Name': self._get_goods_iconbox(good),
                'rowspan="2" | Base production': good.base_production,
                'rowspan="2" | Default price': good.default_market_price,
                'rowspan="2" | Food': good.food,
                'rowspan="2" | RGO type': self.localize(good.method),
                'rowspan="2" | Inflation': 'yes' if good.inflation else '',
                'rowspan="2" | Transport cost': good.transport_cost,
            }
            for pop in self.parser.pop_types.values():
                row[pop.get_wiki_icon()] = self.formatter.format_float(good.demands[pop]) if good.demands[pop] != 0 else 0
            goods.append(row)
        table = self.make_wiki_table(goods, table_classes=['mildtable', 'plainlist'],
                                     one_line_per_cell=True,
                                     remove_empty_columns=True,
                                     )
        table = table.replace('!! [[File:', f'''!! colspan="{len(self.parser.pop_types)}" | Pop demands
|-
! [[File:''', 1)
        return table
    def generate_goods_tags_lists(self):
        tags = {tag for good in self.parser.goods.values() for tag in good.custom_tags}
        result = []
        for tag in sorted(tags):
            result.append(self.surround_with_autogenerated_section(f'goods_list_{tag}', self.get_goods_tag_list(tag)))

        return result

    def get_goods_tag_list(self, tag: str):
        return self.create_wiki_list([good.get_wiki_link_with_icon() for good in sorted(self.parser.goods.values(), key=attrgetter('display_name')) if tag in good.custom_tags])

    def generate_law_tables(self):
        laws_per_category = {cat: [l for l in self.parser.laws.values() if l.law_category == cat] for cat in sorted(set(law.law_category for law in self.parser.laws.values()))}
        result = []
        for category, laws in laws_per_category.items():
            result.append(f'=== {category} ===')
            result.append(self.get_law_table(sorted(laws, key=attrgetter('display_name'))))
        return result

    def get_law_tables(self, section_level: int = None):
        result = {}
        for (io_type, law_category), laws in unsorted_groupby(self.parser.laws.values(), key=attrgetter('io_type', 'law_category')):
            if io_type == '':
                io_type_section = ''
                increased_section_level = 0
            else:
                io_type_section = f'io_{io_type}_'
                increased_section_level = 1
            if section_level is None:
                data = self.get_law_table(laws)
            else:
                data =  self.get_laws_as_sections(laws, section_level + increased_section_level)
            result[f'laws_{io_type_section}{law_category}'] = data

        return result

    def get_law_table(self, laws: Iterable[Law]):
        law_data = [self.get_law_data(law) for law in laws]
        return self.make_wiki_table(law_data, table_classes=['mildtable', 'plainlist'],
                                    one_line_per_cell=True,
                                    remove_empty_columns=True,
                                    )

    def get_law_data(self, law: Law) -> dict[str, str]:
        return {
            'Name': f'{{{{iconbox|{law.display_name}|{law.description}|w=300px|image={law.get_wiki_filename()}}}}}',
            'Potential': self.formatter.format_trigger(law.potential),  # potential: <class 'eu5.eu5lib.Trigger'>
            'Allow': self.formatter.format_trigger(law.allow),  # allow: <class 'common.paradox_parser.Tree'>
            # 'Law Category': law.law_category_loc,  # law_category: <class 'str'>
            'Country': self.parser.localize(law.law_country_group) if law.law_country_group else '',  # law_country_group: <class 'str'>
            'Government type': self.parser.localize(law.law_gov_group) if law.law_gov_group else '',  # law_gov_group: <class 'str'>
            'Religion groups': self.create_wiki_list([self.parser.localize(law_religion_group) for law_religion_group in law.law_religion_group]),
            # law_religion_group: list[str]
            'Locked': self.formatter.format_trigger(law.locked),  # locked: <class 'eu5.eu5lib.Trigger'>
            'Requires Vote': '' if law.requires_vote is None else (
                '[[File:Yes.png|20px|Requires Vote]]' if law.requires_vote else '[[File:No.png|20px|Not Requires Vote]]'),
            # requires_vote: <class 'bool'>
            # 'Type': law.type,  # type  'str'
            'Unique': '' if law.unique is None else '[[File:Yes.png|20px|Unique]]' if law.unique else '[[File:No.png|20px|Not Unique]]',
            # unique: <class 'bool'>
            'Policies': self.get_law_policy_table(law.policies.values()),
        }

    def get_laws_as_sections(self, laws: Iterable[Law], section_level = 3) -> str:
        result = ['']
        ignored_attributes = ['Name', 'Policies']  # added in other ways
        attribute_map = {'Country': 'Only for',
                         'Government type': 'Only for',
                         'Religion groups': 'Requires one of the following religion groups',
                         }
        for law in laws:
            result.append(self.formatter.create_section_heading(law.display_name, section_level))
            result.append(f'{{{{iconbox||{law.description}|image={law.get_wiki_filename()}}}}}')
            law_data = self.get_law_data(law)
            for attribute, value in law_data.items():
                if attribute not in ignored_attributes and value is not None and len(value) > 0:
                    if attribute in attribute_map:
                        attribute = attribute_map[attribute]
                    result.append(f';{attribute}: {value}')
            result.append(law_data['Policies'])


        return '\n'.join(result)

    @staticmethod
    def _format_time_to_implement(policy: LawPolicy, ignore_default_years=2):
        days = policy.days
        weeks = policy.weeks
        months = policy.months
        years = policy.years

        if days >= 365:
            years += days // 365
            days = days % 365
        if weeks >= 52:
            years += weeks // 52
            weeks = weeks % 52
        if months >= 12:
            years += months // 12
            months = months % 12

        if years == ignore_default_years and days == weeks == months == 0:
            return ''
        result = []
        if years > 0:
            result.append(f'{years} years')
        if months > 0:
            result.append(f'{months} months')
        if weeks > 0:
            result.append(f'{weeks} weeks')
        if days > 0:
            result.append(f'{days} days')
        return '\n'.join(result)

    def get_law_policy_table(self, policies: Iterable[LawPolicy]):
        policy_table_data = [{
            'width=20% | Policy': f"'''{policy.display_name}'''\n\n<div class=\"hidem\" style=\"font-style: italic; font-size:smaller;\">{policy.description}</div>",
            'Allow': self.formatter.format_trigger(policy.allow),  # allow: <class 'eu5.eu5lib.Trigger'>
            'Country Modifier': self.format_modifier_section('country_modifier', policy),  # country_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Estate Preferences': self.create_wiki_list([estate_preferences.get_wiki_link_with_icon() for estate_preferences in policy.estate_preferences]),
            # estate_preferences: list[str]
            'Time to implement': self._format_time_to_implement(policy, ignore_default_years=2),
            'On Activate': self.formatter.format_effect(policy.on_activate),  # on_activate: <class 'eu5.eu5lib.Effect'>
            'On Deactivate': self.formatter.format_effect(policy.on_deactivate),  # on_deactivate: <class 'eu5.eu5lib.Effect'>
            'On Pay Price': self.formatter.format_effect(policy.on_pay_price),  # on_pay_price: <class 'eu5.eu5lib.Effect'>
            'On Fully Activated': self.formatter.format_effect(policy.on_fully_activated),  # on_fully_activated: <class 'eu5.eu5lib.Effect'>
            'Potential': self.formatter.format_trigger(policy.potential),  # potential: <class 'eu5.eu5lib.Trigger'>
            'Price': policy.price.format(icon_only=True) if hasattr(policy.price, 'format') else policy.price,  # price: <class 'eu5.eu5lib.Price'>
            # TODO: AI preference wants_this_policy_bias should be included eventually
            # 'Wants This Policy Bias': '' if policy.wants_this_policy_bias is None else policy.wants_this_policy_bias,
            # wants_this_policy_bias
            'Diplomatic Capacity Cost': '' if policy.diplomatic_capacity_cost is None else policy.diplomatic_capacity_cost,
            # diplomatic_capacity_cost: <class 'str'>
            'Gold': '' if policy.gold is None else '[[File:Yes.png|20px|Gold]]' if policy.gold else '[[File:No.png|20px|Not Gold]]',  # gold: <class 'bool'>
            'Manpower': '' if policy.manpower is None else '[[File:Yes.png|20px|Manpower]]' if policy.manpower else '[[File:No.png|20px|Not Manpower]]',
            # manpower: <class 'bool'>
            'Allow Member Annexation': '' if policy.allow_member_annexation is None else '[[File:Yes.png|20px|Allow Member Annexation]]' if policy.allow_member_annexation else '[[File:No.png|20px|Not Allow Member Annexation]]',
            # allow_member_annexation: <class 'bool'>
            'Annexation Speed': '' if policy.annexation_speed is None else policy.annexation_speed,  # annexation_speed: <class 'float'>
            'Can Build Buildings In Members': '' if policy.can_build_buildings_in_members is None else '[[File:Yes.png|20px|Can Build Buildings In Members]]' if policy.can_build_buildings_in_members else '[[File:No.png|20px|Not Can Build Buildings In Members]]',
            # can_build_buildings_in_members: <class 'bool'>
            'Can Build Rgos In Members': '' if policy.can_build_rgos_in_members is None else '[[File:Yes.png|20px|Can Build Rgos In Members]]' if policy.can_build_rgos_in_members else '[[File:No.png|20px|Not Can Build Rgos In Members]]',
            # can_build_rgos_in_members: <class 'bool'>
            'Can Build Roads In Members': '' if policy.can_build_roads_in_members is None else '[[File:Yes.png|20px|Can Build Roads In Members]]' if policy.can_build_roads_in_members else '[[File:No.png|20px|Not Can Build Roads In Members]]',
            # can_build_roads_in_members: <class 'bool'>
            'Has Parliament': '' if policy.has_parliament is None else '[[File:Yes.png|20px|Has Parliament]]' if policy.has_parliament else '[[File:No.png|20px|Not Has Parliament]]',
            # has_parliament: <class 'bool'>
            'International Organization Modifier': self.format_modifier_section('international_organization_modifier', policy),
            # international_organization_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Leader Change Method': '' if policy.leader_change_method is None else policy.leader_change_method,  # leader_change_method: <class 'str'>
            'Leader Change Trigger Type': '' if policy.leader_change_trigger_type is None else policy.leader_change_trigger_type,
            # leader_change_trigger_type: <class 'str'>
            'Leader Type': '' if policy.leader_type is None else policy.leader_type,  # leader_type: <class 'str'>
            'Leadership Election Resolution': '' if policy.leadership_election_resolution is None else policy.leadership_election_resolution,
            # leadership_election_resolution: <class 'str'>
            'Months Between Leader Changes': '' if policy.months_between_leader_changes is None else policy.months_between_leader_changes,
            # months_between_leader_changes: <class 'int'>
            'Opinion Bonus': '' if policy.opinion_bonus is None else policy.opinion_bonus,  # opinion_bonus: <class 'int'>
            'Payments Implemented': self.create_wiki_list(policy.payments_implemented),
            # payments_implemented: list[str]
            'Trust Bonus': '' if policy.trust_bonus is None else policy.trust_bonus,  # trust_bonus: <class 'int'>
        } for policy in policies]
        return "\n" + self.make_wiki_table(policy_table_data, table_classes=['mildtable', 'plainlist'],
                                    one_line_per_cell=True,
                                    remove_empty_columns=True,
                                    )
    def generate_parliament_issue_table(self):
        issues = [issue for issue in self.parser.parliament_issues.values() if issue.type == 'country']
        issue_table_data = [{
            # 'Parliament issue': f'{{{{iconbox|{issue.display_name}|{issue.description}|w=300px|image={issue.get_wiki_filename()}}}}}',
            'Parliament issue': f"'''{issue.display_name}'''\n\n<div class=\"hidem\" style=\"font-style: italic; font-size:smaller;\">{issue.description}</div>",
            'Requirements': self.formatter.format_trigger(issue.potential) + '\n' + self.formatter.format_trigger(issue.allow),
            'Chance': issue.chance.format() if hasattr(issue.chance, 'format') else issue.chance,
            'Estate': '' if issue.estate is None else issue.estate.get_wiki_link_with_icon() if issue.estate else '',
            'Modifiers while the issue is debated': self.format_modifier_section('modifier_when_in_debate', issue),
            'Effects if the issue is being resolved': self.formatter.format_effect(issue.on_debate_passed),
            'Effects if the issue fails': self.formatter.format_effect(issue.on_debate_failed),

            # for IO debates
            # 'Selectable For': self.formatter.format_trigger(issue.selectable_for),
            # selectable_for: <class 'eu5.trigger.Trigger'>
            # 'Special Status': '' if issue.special_status is None else issue.special_status.get_wiki_link_with_icon() if issue.special_status else '',
            # special_status: <class 'eu5.eu5lib.InternationalOrganizationSpecialStatus'>
            # 'AI voting criteria': '' if issue.wants_this_parliament_issue_bias is None else issue.wants_this_parliament_issue_bias.format() if hasattr(
            #     issue.wants_this_parliament_issue_bias, 'format') else issue.wants_this_parliament_issue_bias,
        } for issue in issues]
        return self.make_wiki_table(issue_table_data, table_classes=['mildtable', 'plainlist'],
                                    one_line_per_cell=True,
                                    remove_empty_columns=True,
                                    )

    def _generate_countries_by_region_tables(self):
        countries = [country for country in self.parser.countries.values() if country.capital and country.capital.region and country.capital.region.sub_continent]

        subcontinent_regions: dict = defaultdict(lambda: defaultdict(list))
        for country in countries:
            region = country.capital.region
            subcontinent = region.sub_continent
            subcontinent_regions[subcontinent][region].append(country)

        def format_government(country):
            parts = []

            def _icon_link_from_text(text: str) -> str:
                # Generic fallback: show a template icon and a wiki link to the text
                return f'{{{{icon|{text}}}}} [[{text}]]'

            # Government type (stored in government Tree)
            if hasattr(country, 'government') and country.government and 'type' in country.government:
                gov_type = country.government['type']
                if gov_type in self.parser.government_types:
                    gov_obj = self.parser.government_types[gov_type]
                    parts.append(f'* {gov_obj.get_wiki_link_with_icon()}')
                else:
                    government_display = self.formatter.resolve_nested_localizations(
                        self.parser.localize(gov_type, default=gov_type))
                    parts.append(f'* {_icon_link_from_text(government_display)}')

            # Country type (from setup_data: pop, building, etc.). Only show if NOT settled
            if hasattr(country, 'setup_data') and country.setup_data and 'type' in country.setup_data:
                country_type_key = country.setup_data['type']
                # Skip "location" (settled countries) - only show non-settled types
                if country_type_key != 'location':
                    # Try to resolve as a game concept for proper display name
                    concept = self.parser.game_concepts.get(country_type_key)
                    if concept:
                        # Use concept display name, but link to Country#Country_type
                        display = concept.display_name
                        file_name = f"Country {str(country_type_key).lower()}"
                        link_target = f'Country#Country_type'
                        parts.append(f"* [[File:{file_name}.png|24px|{display}|link={link_target}]] [[{link_target}|{display}]]")
                    else:
                        country_type_display = self.formatter.resolve_nested_localizations(
                            self.parser.localize(country_type_key, default=country_type_key.capitalize()))
                        file_name = f"Country {str(country_type_key).lower()}"
                        link_target = f'Country#Country_type'
                        parts.append(f"* [[File:{file_name}.png|24px|{country_type_display}|link={link_target}]] [[{link_target}|{country_type_display}]]")

            # Country rank
            rank_key = country.country_rank if hasattr(country, 'country_rank') and country.country_rank else self.infer_country_rank_key(country)
            if rank_key:
                if rank_key in self.parser.country_ranks:
                    rank_obj = self.parser.country_ranks[rank_key]
                    # Explicit Rank icon: [[File:Rank <rank>.png]] + link
                    display = rank_obj.display_name
                    link_target = rank_obj.get_wiki_link_target()
                    file_name = f"Rank {display.lower()}"
                    parts.append(f"* [[File:{file_name}.png|24px|{display}|link={link_target}]] [[{link_target}|{display}]]")
                else:
                    rank_display = self.formatter.resolve_nested_localizations(
                        self.parser.localize(rank_key, default=rank_key))
                    file_name = f"Rank {rank_display.lower()}"
                    page = 'Country rank'
                    anchor = rank_display
                    parts.append(f"* [[File:{file_name}.png|24px|{anchor}|link={page}#{anchor}]] [[{page}#{anchor}|{anchor}]]")

            return '\n' + '\n'.join(parts) if parts else ''

        def format_nameable(value):
            if hasattr(value, 'display_name'):
                return value.display_name
            if value:
                return self.formatter.resolve_nested_localizations(self.parser.localize(str(value), default=str(value)))
            return ''

        def format_religion(value):
            """Return icon + custom link for Religion per spec:

            - Catholic ? link to "Catholicism"
            - Non-Catholic ? link to "List of religions#<Display Name>"
            - Preserve icon if Religion entity is available
            - Fallback: formatted name only
            """
            if not value:
                return ''

            def _format_with_target(rel_obj, display_name):
                is_catholic = (getattr(rel_obj, 'name', '').lower() == 'catholic') or (display_name.lower() == 'catholicism')
                link_target = 'Catholicism' if is_catholic else f'List of religions#{display_name}'
                # Use entity icon when available; otherwise no icon
                if hasattr(rel_obj, 'get_wiki_file_tag'):
                    icon = rel_obj.get_wiki_file_tag('24px', link=link_target) or ''
                else:
                    icon = ''
                text = f'[[{link_target}|{display_name}]]'
                return f'{icon} {text}'.strip()

            # Entity path
            if hasattr(value, 'display_name'):
                return _format_with_target(value, value.display_name)

            # Key path ? resolve entity
            if isinstance(value, str):
                rel = self.parser.religions.get(value)
                if rel:
                    return _format_with_target(rel, rel.display_name)
                # Fallback to using localized display name without icon
                display_name = self.parser.localize(value)
                is_catholic = value.lower() == 'catholic' or display_name.lower() == 'catholicism'
                link_target = 'Catholicism' if is_catholic else f'List of religions#{display_name}'
                return f'[[{link_target}|{display_name}]]'

            # Final fallback
            return format_nameable(value)

        def build_country_table_rows(countries_list):
            """Build table rows for a list of countries."""
            table_rows = []
            for country in countries_list:
                notes, nested_details = get_country_notes(country)

                # Compose notes cell with optional nested details
                if notes and nested_details:
                    notes_cell = self.create_wiki_list(notes) + '\n' + self.create_wiki_list(nested_details, indent=2)
                elif notes:
                    notes_cell = self.create_wiki_list(notes)
                else:
                    notes_cell = ''

                # Country cell: image + bold name - NO LINKS in Country Name column
                country_display = country.display_name
                if country.name == 'MAM':  # ensure Egypt renders without tag suffix
                    country_display = 'Egypt'
                
                # Country name column should have no links at all
                country_image = f'[[File:{country_display}.png|100px]]'
                country_cell = f"{country_image} '''{country_display}'''"
                
                table_rows.append({
                    'Country': country_cell,
                    'Tag': country.name,
                    'Government': format_government(country),
                    'Religion': format_religion(country.religion_definition),
                    'Culture': format_nameable(country.culture_definition),
                    'Capital': format_nameable(country.capital),
                    'Notes': notes_cell,
                })
            return table_rows

        def get_country_notes(country):
            """Extract important notes for a country including:
            - Subject status and type
            - IO membership (excluding Catholic IO)
            - Non-existent countries (cores conquered but none controlled)
            
            Returns a tuple: (top_level_notes: list[str], nested_details: list[str]|None)
            """
            notes: list[str] = []
            nested_details: list[str] | None = None

            # Formable: match by formable country script name to country tag
            # Defer appending so it appears beneath other notes
            is_formable = any(
                fc.country_name == country.name
                for fc in self.parser.formable_countries.values()
            )

            # Non-existent country: has cores conquered by others but doesn't control any of its own cores
            has_conquered_cores = hasattr(country, 'our_cores_conquered_by_others') and len(country.our_cores_conquered_by_others) > 0
            has_owned_cores = (
                (hasattr(country, 'own_control_core') and len(country.own_control_core) > 0) or
                (hasattr(country, 'own_core') and len(country.own_core) > 0) or
                (hasattr(country, 'own_conquered') and len(country.own_conquered) > 0) or
                (hasattr(country, 'own_control_conquered') and len(country.own_control_conquered) > 0) or
                (hasattr(country, 'own_control_integrated') and len(country.own_control_integrated) > 0) or
                (hasattr(country, 'own_control_colony') and len(country.own_control_colony) > 0) or
                (hasattr(country, 'control') and len(country.control) > 0)
            )
            
            if has_conquered_cores and not has_owned_cores:
                notes.append('Does not exist in 1337')

            # Subject/overlord and IO memberships
            if hasattr(self.parser, 'diplomacy_relationships'):
                diplo_rels = self.parser.diplomacy_relationships
                io_member_laws = diplo_rels.get('io_member_laws', {})

                # Subject relationship
                if country.name in diplo_rels['overlords']:
                    overlord_tag = diplo_rels['overlords'][country.name]
                    subject_type = diplo_rels['subject_types'].get(country.name, 'subject')
                    if subject_type in self.parser.subject_types:
                        st = self.parser.subject_types[subject_type]
                        subject_display = st.get_wiki_link_with_icon()
                    else:
                        subject_display = subject_type.capitalize()

                    if overlord_tag in self.parser.countries:
                        overlord_country = self.parser.countries[overlord_tag]
                        overlord_flag = overlord_country.get_wiki_link_with_icon()
                    else:
                        overlord_flag = f'{{{{flag|{overlord_tag}}}}}'

                    notes.append(f'{subject_display} of {overlord_flag}')

                # IO membership (skip Catholic-specific entries)
                if country.name in diplo_rels['io_members']:
                    io_names = diplo_rels['io_members'][country.name]
                    for io_name in io_names:
                        if io_name.lower() not in ['catholic_church', 'papacy']:
                            if io_name in self.parser.international_organizations:
                                # Special handling for Hindu Branch to show specific branch policy
                                if io_name.lower() == 'hindu_branch':
                                    branch_display = None
                                    branch_laws = io_member_laws.get(country.name, {}).get(io_name, [])
                                    if branch_laws:
                                        branch_key = branch_laws[0]
                                        if branch_key in self.parser.law_policies:
                                            branch_display = self.parser.law_policies[branch_key].display_name
                                        elif branch_key in self.parser.laws:
                                            branch_display = self.parser.laws[branch_key].display_name
                                        else:
                                            branch_display = branch_key.replace('_', ' ').title()

                                    if branch_display:
                                        branch_file = branch_display.lower()
                                        notes.append(f'Member of [[File:IO {branch_file}.png|24px]] [[International organization#Hindu Branches|{branch_display}]]')
                                        continue

                                io_obj = self.parser.international_organizations[io_name]
                                notes.append(f'Member of {io_obj.get_wiki_link_with_icon()}')
                            else:
                                notes.append(f'Member of {{{{iconify|{io_name}}}}}')

            # Ensure Formable note appears last
            if is_formable:
                notes.append('[[File:Country rank.png|24px|link=Formable countries]] [[Formable countries|Formable Country]]')

            return notes, nested_details

        outputs: dict[str, str] = {}
        # Track German regions for separate output
        german_region_names = {'north_german_region', 'south_german_region'}
        german_regions_data = {}
        # Track Japan region for separate output
        japan_region_names = {'japan_region'}
        japan_regions_data = {}
        # Track Africa subcontinents for consolidated output
        africa_data: dict[str, list] = {}
        
        for subcontinent in sorted(subcontinent_regions.keys(), key=lambda sc: sc.display_name):
            # Check if this subcontinent is in Africa
            is_africa = subcontinent.continent and subcontinent.continent.display_name.lower() == 'africa'
            
            # Initialize Africa data structure if needed
            if is_africa and subcontinent.name not in africa_data:
                africa_data[subcontinent] = []
            
            # Remove subcontinent header; start directly with region sections
            subcontinent_lines: list[str] = []

            for region in sorted(subcontinent_regions[subcontinent].keys(), key=lambda r: r.display_name):
                # Separate German regions from Western Europe
                if region.name in german_region_names:
                    german_regions_data[region] = subcontinent_regions[subcontinent][region]
                    continue
                
                # Separate Japan region from East Asia
                if region.name in japan_region_names:
                    japan_regions_data[region] = subcontinent_regions[subcontinent][region]
                    continue
                
                countries_in_region = sorted(subcontinent_regions[subcontinent][region], key=lambda country: country.display_name)
                if not countries_in_region:
                    continue

                table_rows = build_country_table_rows(countries_in_region)

                # For Africa, use level 3 headers for regions (level 2 reserved for subcontinent)
                # For other continents, use level 2 headers
                if is_africa:
                    region_header = f'=== {region.display_name} ==='
                else:
                    region_header = f'== {region.display_name} =='
                
                subcontinent_lines.append(region_header)
                subcontinent_lines.append(self.make_wiki_table(table_rows,
                                                                table_classes=['mildtable', 'plainlist'],
                                                                one_line_per_cell=True,
                                                                ))
                subcontinent_lines.append('')  # blank line to terminate table before next heading

            # Store Africa subcontinents' data for consolidation, others write directly to outputs
            if is_africa:
                africa_data[subcontinent] = subcontinent_lines
            else:
                outputs[subcontinent.name] = '\n'.join(subcontinent_lines).rstrip()

        # Consolidate Africa subcontinents into one file
        if africa_data:
            africa_lines: list[str] = []
            for subcontinent in sorted(africa_data.keys(), key=lambda sc: sc.display_name):
                # Add subcontinent header (level 2)
                africa_lines.append(f'== {subcontinent.display_name} ==')
                # Add all regions and tables for this subcontinent
                africa_lines.extend(africa_data[subcontinent])
            
            outputs['africa'] = '\n'.join(africa_lines).rstrip()

        # Generate separate output for German regions
        if german_regions_data:
            german_lines: list[str] = []
            for region in sorted(german_regions_data.keys(), key=lambda r: r.display_name):
                countries_in_region = sorted(german_regions_data[region], key=lambda country: country.display_name)
                if not countries_in_region:
                    continue

                table_rows = build_country_table_rows(countries_in_region)
                german_lines.append(f'== {region.display_name} ==')
                german_lines.append(self.make_wiki_table(table_rows,
                                                          table_classes=['mildtable', 'plainlist'],
                                                          one_line_per_cell=True,
                                                          ))
                german_lines.append('')  # blank line to terminate table before next heading

            outputs['german'] = '\n'.join(german_lines).rstrip()

        # Generate separate output for Japan region
        if japan_regions_data:
            japan_lines: list[str] = []
            for region in sorted(japan_regions_data.keys(), key=lambda r: r.display_name):
                countries_in_region = sorted(japan_regions_data[region], key=lambda country: country.display_name)
                if not countries_in_region:
                    continue

                table_rows = build_country_table_rows(countries_in_region)
                japan_lines.append(f'== {region.display_name} ==')
                japan_lines.append(self.make_wiki_table(table_rows,
                                                         table_classes=['mildtable', 'plainlist'],
                                                         one_line_per_cell=True,
                                                         ))
                japan_lines.append('')  # blank line to terminate table before next heading

            outputs['japan'] = '\n'.join(japan_lines).rstrip()

        return outputs


    def generate_countries(self):
        """Alias so the module can be run with `python -m eu5.generate_tables countries`."""
        return self._generate_countries_by_region_tables()


    # AUTOGENERATED

    def generate_advances_table(self):
        advancess = self.parser.advances.values()
        advances_table_data = [{
            'Name': f'{{{{iconbox|{advances.display_name}|{advances.description}|w=300px|image={advances.get_wiki_filename()}}}}}',
            'Age': advances.age,  # age: <class 'str'>
            'Ai Preference Tags': 'grota must implement or delete',  # ai_preference_tags: <class 'list'>
            'Ai Weight': '' if advances.ai_weight is None else self.create_wiki_list([f'{k}: ...' for k in advances.ai_weight.keys()]) if advances.ai_weight else '',  # ai_weight: <class 'common.paradox_parser.Tree'>
            'Allow': self.formatter.format_trigger(advances.allow),  # allow: <class 'eu5.trigger.Trigger'>
            'Allow Children': '[[File:Yes.png|20px|Allow Children]]' if advances.allow_children else '[[File:No.png|20px|Not Allow Children]]',  # allow_children: <class 'bool'>
            'Country Type': '' if advances.country_type is None else advances.country_type,  # country_type: <class 'str'>
            'Depth': '' if advances.depth is None else advances.depth,  # depth: <class 'int'>
            'Age Specialization': '' if advances.age_specialization is None else advances.age_specialization,  # age_specialization: <class 'str'>
            'Government': '' if advances.government is None else advances.government,  # government: <class 'str'>
            'In Tree Of': '' if advances.in_tree_of is None else advances.in_tree_of,  # in_tree_of: typing.Any
            'Modifier While Progressing': self.format_modifier_section('modifier_while_progressing', advances),  # modifier_while_progressing: list[eu5.eu5lib.Eu5Modifier]
            'Potential': self.formatter.format_trigger(advances.potential),  # potential: <class 'eu5.trigger.Trigger'>
            'Requires': self.create_wiki_list([requires.get_wiki_link_with_icon() if requires else '' for requires in advances.requires]),  # requires: list[eu5.eu5lib.Advance]
            'Research Cost': '' if advances.research_cost is None else advances.research_cost,  # research_cost: <class 'float'>
            'Starting Technology Level': advances.starting_technology_level,  # starting_technology_level: <class 'int'>
            'Unlock Ability': self.create_wiki_list([unlock_ability for unlock_ability in advances.unlock_ability]),  # unlock_ability: list[str]
            'Unlock Building': self.create_wiki_list([unlock_building for unlock_building in advances.unlock_building]),  # unlock_building: list[str]
            'Unlock Cabinet Action': self.create_wiki_list([unlock_cabinet_action for unlock_cabinet_action in advances.unlock_cabinet_action]),  # unlock_cabinet_action: list[str]
            'Unlock Casus Belli': self.create_wiki_list([unlock_casus_belli for unlock_casus_belli in advances.unlock_casus_belli]),  # unlock_casus_belli: list[str]
            'Unlock Country Interaction': self.create_wiki_list([unlock_country_interaction for unlock_country_interaction in advances.unlock_country_interaction]),  # unlock_country_interaction: list[str]
            'Unlock Diplomacy': self.create_wiki_list([unlock_diplomacy for unlock_diplomacy in advances.unlock_diplomacy]),  # unlock_diplomacy: list[str]
            'Unlock Estate Privilege': self.create_wiki_list([unlock_estate_privilege for unlock_estate_privilege in advances.unlock_estate_privilege]),  # unlock_estate_privilege: list[str]
            'Unlock Government Reform': self.create_wiki_list([unlock_government_reform for unlock_government_reform in advances.unlock_government_reform]),  # unlock_government_reform: list[str]
            'Unlock Heir Selection': self.create_wiki_list([unlock_heir_selection for unlock_heir_selection in advances.unlock_heir_selection]),  # unlock_heir_selection: list[str]
            'Unlock Law': self.create_wiki_list([unlock_law for unlock_law in advances.unlock_law]),  # unlock_law: list[str]
            'Unlock Levy': self.create_wiki_list([unlock_levy.display_name for unlock_levy in advances.unlock_levy]),  # unlock_levy: list[str]
            'Unlock Policy': self.create_wiki_list([unlock_policy for unlock_policy in advances.unlock_policy]),  # unlock_policy: list[str]
            'Unlock Production Method': self.create_wiki_list([unlock_production_method for unlock_production_method in advances.unlock_production_method]),  # unlock_production_method: list[str]
            'Unlock Road Type': self.create_wiki_list([unlock_road_type for unlock_road_type in advances.unlock_road_type]),  # unlock_road_type: list[str]
            'Unlock Subject Type': self.create_wiki_list([unlock_subject_type for unlock_subject_type in advances.unlock_subject_type]),  # unlock_subject_type: list[str]
            'Unlock Unit': self.create_wiki_list([unlock_unit for unlock_unit in advances.unlock_unit]),  # unlock_unit: list[str]
        } for advances in advancess]
        return self.make_wiki_table(advances_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_cabinet_actions_table(self):
        cabinet_actionss = self.parser.cabinet_actions.values()
        cabinet_actions_table_data = [{
            'Name': f'{{{{iconbox|{cabinet_actions.display_name}|{cabinet_actions.description}|w=300px|image={cabinet_actions.get_wiki_filename()}}}}}',
            'Ability': cabinet_actions.ability,  # ability: <class 'str'>
            'Ai Will Do': '' if cabinet_actions.ai_will_do is None else cabinet_actions.ai_will_do.format() if hasattr(cabinet_actions.ai_will_do, 'format') else cabinet_actions.ai_will_do,  # ai_will_do: <class 'eu5.eu5lib.ScriptValue'>
            'Allow': self.formatter.format_trigger(cabinet_actions.allow),  # allow: <class 'eu5.trigger.Trigger'>
            'Allow Multiple': '' if cabinet_actions.allow_multiple is None else '[[File:Yes.png|20px|Allow Multiple]]' if cabinet_actions.allow_multiple else '[[File:No.png|20px|Not Allow Multiple]]',  # allow_multiple: <class 'bool'>
            'Country Modifier': self.format_modifier_section('country_modifier', cabinet_actions),  # country_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Days': cabinet_actions.days,  # days: <class 'int'>
            'Forbid For Automation': '[[File:Yes.png|20px|Forbid For Automation]]' if cabinet_actions.forbid_for_automation else '[[File:No.png|20px|Not Forbid For Automation]]',  # forbid_for_automation: <class 'bool'>
            'Is Finished': self.formatter.format_trigger(cabinet_actions.is_finished),  # is_finished: <class 'eu5.trigger.Trigger'>
            'Location Modifier': self.format_modifier_section('location_modifier', cabinet_actions),  # location_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Map Marker': '' if cabinet_actions.map_marker is None else self.create_wiki_list([f'{k}: ...' for k in cabinet_actions.map_marker.keys()]) if cabinet_actions.map_marker else '',  # map_marker: <class 'common.paradox_parser.Tree'>
            'Min': '' if cabinet_actions.min is None else cabinet_actions.min,  # min: <class 'int'>
            'On Activate': self.formatter.format_effect(cabinet_actions.on_activate),  # on_activate: <class 'eu5.effect.Effect'>
            'On Deactivate': self.formatter.format_effect(cabinet_actions.on_deactivate),  # on_deactivate: <class 'eu5.effect.Effect'>
            'On Fully Activated': self.formatter.format_effect(cabinet_actions.on_fully_activated),  # on_fully_activated: <class 'eu5.effect.Effect'>
            'Potential': self.formatter.format_trigger(cabinet_actions.potential),  # potential: <class 'eu5.trigger.Trigger'>
            'Progress': '' if cabinet_actions.progress is None else cabinet_actions.progress.format() if hasattr(cabinet_actions.progress, 'format') else cabinet_actions.progress,  # progress: <class 'eu5.eu5lib.ScriptValue'>
            'Province Modifier': self.format_modifier_section('province_modifier', cabinet_actions),  # province_modifier: list[eu5.eu5lib.Eu5Modifier]
            # 'Select Trigger': '' if cabinet_actions.select_trigger is None else self.create_wiki_list([f'{k}: ...' for k in cabinet_actions.select_trigger.keys()]) if cabinet_actions.select_trigger else '',  # select_trigger: <class 'common.paradox_parser.Tree'>
            'Societal Values': cabinet_actions.societal_values,  # societal_values: <class 'float'>
            'Years': cabinet_actions.years,  # years: <class 'int'>
        } for cabinet_actions in cabinet_actionss]
        return self.make_wiki_table(cabinet_actions_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_casus_belli_table(self):
        casus_bellis = self.parser.casus_belli.values()
        casus_belli_table_data = [{
            'Name': f'{{{{iconbox|{casus_belli.display_name}|{casus_belli.description}|w=300px|image={casus_belli.get_wiki_filename()}}}}}',
            'Additional War Enthusiasm': casus_belli.additional_war_enthusiasm,  # additional_war_enthusiasm: <class 'float'>
            'Additional War Enthusiasm Attacker': casus_belli.additional_war_enthusiasm_attacker,  # additional_war_enthusiasm_attacker: <class 'float'>
            'Additional War Enthusiasm Defender': casus_belli.additional_war_enthusiasm_defender,  # additional_war_enthusiasm_defender: <class 'float'>
            'Ai Cede Location Desire': '' if casus_belli.ai_cede_location_desire is None else casus_belli.ai_cede_location_desire.format() if hasattr(casus_belli.ai_cede_location_desire, 'format') else casus_belli.ai_cede_location_desire,  # ai_cede_location_desire: <class 'eu5.eu5lib.ScriptValue'>
            'Ai Cede Province Desire': '' if casus_belli.ai_cede_province_desire is None else casus_belli.ai_cede_province_desire.format() if hasattr(casus_belli.ai_cede_province_desire, 'format') else casus_belli.ai_cede_province_desire,  # ai_cede_province_desire: <class 'eu5.eu5lib.ScriptValue'>
            'Ai Selection Desire': '' if casus_belli.ai_selection_desire is None else casus_belli.ai_selection_desire.format() if hasattr(casus_belli.ai_selection_desire, 'format') else casus_belli.ai_selection_desire,  # ai_selection_desire: <class 'eu5.eu5lib.ScriptValue'>
            'Ai Subjugation Desire': casus_belli.ai_subjugation_desire,  # ai_subjugation_desire: <class 'int'>
            'Allow Creation': self.formatter.format_trigger(casus_belli.allow_creation),  # allow_creation: <class 'eu5.trigger.Trigger'>
            'Allow Declaration': self.formatter.format_trigger(casus_belli.allow_declaration),  # allow_declaration: <class 'eu5.trigger.Trigger'>
            'Allow Ports For Reach Ai': '[[File:Yes.png|20px|Allow Ports For Reach Ai]]' if casus_belli.allow_ports_for_reach_ai else '[[File:No.png|20px|Not Allow Ports For Reach Ai]]',  # allow_ports_for_reach_ai: <class 'bool'>
            'Allow Release Areas': '[[File:Yes.png|20px|Allow Release Areas]]' if casus_belli.allow_release_areas else '[[File:No.png|20px|Not Allow Release Areas]]',  # allow_release_areas: <class 'bool'>
            'Allow Separate Peace': '[[File:Yes.png|20px|Allow Separate Peace]]' if casus_belli.allow_separate_peace else '[[File:No.png|20px|Not Allow Separate Peace]]',  # allow_separate_peace: <class 'bool'>
            'Antagonism Reduction Per Warworth Defender': casus_belli.antagonism_reduction_per_warworth_defender,  # antagonism_reduction_per_warworth_defender: <class 'float'>
            'Can Expire': '[[File:Yes.png|20px|Can Expire]]' if casus_belli.can_expire else '[[File:No.png|20px|Not Can Expire]]',  # can_expire: <class 'bool'>
            'Cut Down In Size Cb': '[[File:Yes.png|20px|Cut Down In Size Cb]]' if casus_belli.cut_down_in_size_cb else '[[File:No.png|20px|Not Cut Down In Size Cb]]',  # cut_down_in_size_cb: <class 'bool'>
            'Max Warscore From Battles': casus_belli.max_warscore_from_battles,  # max_warscore_from_battles: <class 'int'>
            'No Cb': '' if casus_belli.no_cb is None else '[[File:Yes.png|20px|No Cb]]' if casus_belli.no_cb else '[[File:No.png|20px|Not No Cb]]',  # no_cb: <class 'bool'>
            'Province': self.formatter.format_trigger(casus_belli.province),  # province: <class 'eu5.trigger.Trigger'>
            'Speed': casus_belli.speed,  # speed: <class 'float'>
            'Trade': '[[File:Yes.png|20px|Trade]]' if casus_belli.trade else '[[File:No.png|20px|Not Trade]]',  # trade: <class 'bool'>
            'Visible': self.formatter.format_trigger(casus_belli.visible),  # visible: <class 'eu5.trigger.Trigger'>
            'War Goal Type': casus_belli.war_goal_type.get_wiki_link_with_icon() if casus_belli.war_goal_type else '',  # war_goal_type: <class 'eu5.eu5lib.Wargoal'>
        } for casus_belli in casus_bellis]
        return self.make_wiki_table(casus_belli_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_cultures_table(self):
        culturess = sorted(self.parser.cultures.values(), key=lambda culture: (culture.culture_groups, culture.display_name))
        cultures_table_data = [{
            'Name': f' style="background-color: {cultures.color.get_css_color_string() if cultures.color else "white"}" | ' + cultures.display_name,
            'Adjective Keys': self.create_wiki_list([adjective_keys for adjective_keys in cultures.adjective_keys]),  # adjective_keys: list[str]
            'Character Modifier': self.format_modifier_section('character_modifier', cultures),  # character_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Country Modifier': self.format_modifier_section('country_modifier', cultures),  # country_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Culture Groups': self.create_wiki_list([culture_groups.display_name if culture_groups else '' for culture_groups in cultures.culture_groups]),  # culture_groups: list[eu5.eu5lib.CultureGroup]
            'Dynasty Name Type': cultures.dynasty_name_type,  # dynasty_name_type: <class 'str'>
            'Language': cultures.language if isinstance(cultures.language, str) else cultures.language.display_name if cultures.language else '',  # language: <class 'eu5.eu5lib.Language'>
            'Location Modifier': self.format_modifier_section('location_modifier', cultures),  # location_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Noun Keys': self.create_wiki_list([noun_keys for noun_keys in cultures.noun_keys]),  # noun_keys: list[str]
            'Opinions': '' if cultures.opinions is None else self.create_wiki_list([f'{k}: ...' for k in cultures.opinions.keys()]) if cultures.opinions else '',  # opinions: <class 'common.paradox_parser.Tree'>
            'Tags': self.create_wiki_list([tags for tags in cultures.tags]),  # tags: list[str]
            'Use Patronym': '[[File:Yes.png|20px|Use Patronym]]' if cultures.use_patronym else '[[File:No.png|20px|Not Use Patronym]]',  # use_patronym: <class 'bool'>
        } for cultures in culturess]
        return self.make_wiki_table(cultures_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_government_reforms_table(self):
        government_reformss = self.parser.government_reforms.values()
        
        # Split into major and minor reforms
        major_reforms = [reform for reform in government_reformss if reform.major]
        minor_reforms = [reform for reform in government_reformss if not reform.major]
        
        result = []
        
        # Process minor reforms
        if minor_reforms:
            result.append('== Minor ==')
            country_specific_minor = [r for r in minor_reforms if self._get_country_from_reform(r) is not None]
            general_minor = [r for r in minor_reforms if self._get_country_from_reform(r) is None]
            
            # General minor reforms table
            if general_minor:
                government_reforms_table_data = [self._get_government_reform_row(government_reforms) for government_reforms in general_minor]
                result.append(self.make_wiki_table(government_reforms_table_data, table_classes=['mildtable', 'plainlist'],
                                                    one_line_per_cell=True,
                                                    remove_empty_columns=True,
                                                    ))
            
            # Country-specific minor reforms table
            if country_specific_minor:
                result.append('=== Country specific ===')
                government_reforms_table_data = [self._get_government_reform_row(government_reforms, country=self._get_country_from_reform(government_reforms)) for government_reforms in country_specific_minor]
                result.append(self.make_wiki_table(government_reforms_table_data, table_classes=['mildtable', 'plainlist'],
                                                    one_line_per_cell=True,
                                                    remove_empty_columns=True,
                                                    ))
        
        # Process major reforms
        if major_reforms:
            result.append('== Major ==')
            country_specific_major = [r for r in major_reforms if self._get_country_from_reform(r) is not None]
            general_major = [r for r in major_reforms if self._get_country_from_reform(r) is None]
            
            # General major reforms table
            if general_major:
                government_reforms_table_data = [self._get_government_reform_row(government_reforms) for government_reforms in general_major]
                result.append(self.make_wiki_table(government_reforms_table_data, table_classes=['mildtable', 'plainlist'],
                                                    one_line_per_cell=True,
                                                    remove_empty_columns=True,
                                                    ))
            
            # Country-specific major reforms table
            if country_specific_major:
                result.append('=== Country specific ===')
                government_reforms_table_data = [self._get_government_reform_row(government_reforms, country=self._get_country_from_reform(government_reforms)) for government_reforms in country_specific_major]
                result.append(self.make_wiki_table(government_reforms_table_data, table_classes=['mildtable', 'plainlist'],
                                                    one_line_per_cell=True,
                                                    remove_empty_columns=True,
                                                    ))
        
        return result
    
    def _get_country_from_reform(self, reform):
        """Extract country tag(s) from has_or_had_tag requirement if present"""
        for trigger_obj in [reform.allow, reform.potential, reform.locked]:
            if trigger_obj is None:
                continue
            countries = self._find_has_or_had_tag_in_trigger(trigger_obj)
            if countries:
                return countries
        return None
    
    def _find_has_or_had_tag_in_trigger(self, trigger_tree):
        """Recursively search trigger tree for has_or_had_tag and return the country tag(s)"""
        from common.paradox_parser import Tree
        
        if not isinstance(trigger_tree, Tree):
            return None
        
        for key, value in trigger_tree:
            if key == 'has_or_had_tag':
                # Can be a single string or a list of strings
                return value
            elif isinstance(value, Tree):
                result = self._find_has_or_had_tag_in_trigger(value)
                if result:
                    return result
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Tree):
                        result = self._find_has_or_had_tag_in_trigger(item)
                        if result:
                            return result
        
        return None
    
    def _get_government_reform_row(self, government_reforms, country=None):
        effects_parts = []

        country_mod = self.format_modifier_section('country_modifier', government_reforms)
        if country_mod:
            effects_parts.append(('Country Modifier', country_mod))

        location_mod = self.format_modifier_section('location_modifier', government_reforms)
        if location_mod:
            effects_parts.append(('Location Modifier', location_mod))

        on_activate = self.formatter.format_effect(government_reforms.on_activate)
        if on_activate:
            effects_parts.append(('On Activate', on_activate))

        on_deactivate = self.formatter.format_effect(government_reforms.on_deactivate)
        if on_deactivate:
            effects_parts.append(('On Deactivate', on_deactivate))

        # Build effects_combined based on number of sections
        if not effects_parts:
            effects_combined = ''
        elif len(effects_parts) == 1:
            # Single effect type - no heading
            effects_combined = effects_parts[0][1]
        else:
            # Multiple effect types - use nested bullet list with bold headings
            effects_lines = []
            for title, content in effects_parts:
                effects_lines.append(f"* '''{title}:'''")
                effects_lines.extend(self._nest_wiki_list(content, extra_depth=1))
            effects_combined = '\n' + '\n'.join(effects_lines)
        
        # Build requirements_combined with similar logic but handling locked conditions specially
        government_text = '' if government_reforms.government is None else government_reforms.government.get_wiki_link_with_icon() if government_reforms.government else ''
        societal_values_text = self.create_wiki_list([sv for sv in government_reforms.societal_values]) if government_reforms.societal_values else ''
        
        # Filter out has_or_had_tag from potential, allow, and locked when country-specific
        potential_text = self._format_trigger_filtered(government_reforms.potential, country)
        allow_text = self._format_trigger_filtered(government_reforms.allow, country)
        locked_text = self._format_trigger_filtered(government_reforms.locked, country)
        
        requirements_parts = []
        if government_text:
            requirements_parts.append(('Government', government_text))
        if societal_values_text:
            requirements_parts.append(('Societal Values', societal_values_text))
        if potential_text:
            requirements_parts.append(('Potential', potential_text))
        if allow_text:
            requirements_parts.append(('Allow', allow_text))
        
        # Build requirements_combined based on whether there's a locked condition
        if not requirements_parts and not locked_text:
            requirements_combined = ''
        elif not locked_text:
            # No locked condition - just combine all requirements without headings
            requirements_combined = '\n'.join([content for title, content in requirements_parts])
        else:
            # Has locked condition - split into unlocked and locked sections
            unlocked_lines = []
            if requirements_parts:
                unlocked_lines.append(f"* '''Reform is unlocked as long as:'''")
                for title, content in requirements_parts:
                    unlocked_lines.extend(self._nest_wiki_list(content, extra_depth=1))
            
            locked_lines = []
            locked_lines.append(f"* '''Reform is locked as long as:'''")
            locked_lines.extend(self._nest_wiki_list(locked_text, extra_depth=1))
            
            if unlocked_lines:
                requirements_combined = '\n' + '\n'.join(unlocked_lines) + '\n\n----\n\n' + '\n'.join(locked_lines)
            else:
                requirements_combined = '\n' + '\n'.join(locked_lines)
        
        row_dict = {
            'Name': f'{{{{iconbox|{government_reforms.display_name}|{government_reforms.description}|w=300px|image={government_reforms.get_wiki_filename()}}}}}',
            'Age': '' if government_reforms.age is None else government_reforms.age.get_wiki_link_with_icon() if government_reforms.age else '',  # age: <class 'eu5.eu5lib.Age'>
            'Effects': effects_combined,  # merged: country_modifier, location_modifier, on_activate, on_deactivate
            'Requirements': requirements_combined,  # merged: government, societal_values, potential, allow, locked
            # 'Allow': allow_text,  # allow: <class 'eu5.trigger.Trigger'>  [MERGED INTO Requirements]
            # 'Country Modifier': self.format_modifier_section('country_modifier', government_reforms),  # country_modifier: list[eu5.eu5lib.Eu5Modifier]  [MERGED INTO Effects]
            # 'Government': government_text,  # government: <class 'eu5.eu5lib.GovernmentType'>  [MERGED INTO Requirements]
            # 'Location Modifier': self.format_modifier_section('location_modifier', government_reforms),  # location_modifier: list[eu5.eu5lib.Eu5Modifier]  [MERGED INTO Effects]
            # 'Locked': locked_text,  # locked: <class 'eu5.trigger.Trigger'>  [MERGED INTO Requirements]
            # 'On Activate': self.formatter.format_effect(government_reforms.on_activate),  # on_activate: <class 'eu5.effect.Effect'>  [MERGED INTO Effects]
            # 'On Deactivate': self.formatter.format_effect(government_reforms.on_deactivate),  # on_deactivate: <class 'eu5.effect.Effect'>  [MERGED INTO Effects]
            # 'Potential': potential_text,  # potential: <class 'eu5.trigger.Trigger'>  [MERGED INTO Requirements]
            # 'Societal Values': self.create_wiki_list([societal_values for societal_values in government_reforms.societal_values]),  # societal_values: list[str]  [MERGED INTO Requirements]
            'Years': (f'{government_reforms.years} years' if government_reforms.years else f'{government_reforms.months} months' if government_reforms.months else ''),  # years: <class 'float'>
        }
        
        # Add Country column if this is a country-specific reform
        if country:
            country_flags = self._format_country_flags(country)
            row_dict['Country'] = country_flags
        
        return row_dict
    
    def _format_country_flags(self, country_data):
        """Format country tag(s) as flag(s). Handles both single strings and lists."""
        # Normalize to list
        countries = country_data if isinstance(country_data, list) else [country_data]

        # Remove 'c:' prefix from each country tag
        countries = [c.removeprefix('c:') for c in countries]

        # Create flag wiki markup for each country
        flag_list = []
        for country_tag in countries:
            country_name = self._get_country_display_name(country_tag)
            flag_list.append(f'{{{{flag|{country_name}}}}}')

        # Return as wiki list if multiple countries, otherwise single flag
        if len(flag_list) == 1:
            return flag_list[0]
        else:
            return self.create_wiki_list(flag_list)

    def _get_country_display_name(self, country_tag: str) -> str:
        """Return localized country name for flag template, falling back to tag."""
        # Prefer full country set (including formables) if available
        country_lookup = getattr(self.parser, 'countries_including_formables', None) or self.parser.countries

        # Normalize tag
        norm_tag = country_tag.upper()

        if norm_tag in country_lookup:
            country_obj = country_lookup[norm_tag]
            # display_name already localized and may include tag suffix; strip suffix if present
            return country_obj.display_name.split('(')[0].strip()

        # Fallback: try localization on tag itself
        localized = self.parser.localize(norm_tag)
        if localized and localized != norm_tag:
            return localized

        return norm_tag
    
    def _format_trigger_filtered(self, trigger_tree, country):
        """Format trigger while filtering out has_or_had_tag if country-specific"""
        if trigger_tree is None:
            return ''
        
        if not country:
            return self.formatter.format_trigger(trigger_tree)
        
        # Filter out has_or_had_tag entries for country-specific reforms
        filtered_tree = self._filter_has_or_had_tag(trigger_tree)
        return self.formatter.format_trigger(filtered_tree) if filtered_tree else ''
    
    def _filter_has_or_had_tag(self, trigger_tree):
        """Remove has_or_had_tag entries from trigger tree"""
        from common.paradox_parser import Tree
        
        if not isinstance(trigger_tree, Tree):
            return trigger_tree
        
        filtered = Tree({})
        for key, value in trigger_tree:
            if key == 'has_or_had_tag':
                # Skip this entry
                continue
            elif isinstance(value, Tree):
                filtered_value = self._filter_has_or_had_tag(value)
                if filtered_value and len(filtered_value) > 0:
                    filtered[key] = filtered_value
            elif isinstance(value, list):
                filtered_list = []
                for item in value:
                    if isinstance(item, Tree):
                        filtered_item = self._filter_has_or_had_tag(item)
                        if filtered_item and len(filtered_item) > 0:
                            filtered_list.append(filtered_item)
                    else:
                        filtered_list.append(item)
                if filtered_list:
                    filtered[key] = filtered_list
            else:
                filtered[key] = value
        
        return filtered

    def _nest_wiki_list(self, text: str, extra_depth: int = 1) -> list[str]:
        """Indent existing wiki-list style text by extra_depth levels.

        Preserves existing bullet depth (leading `*`) and adds extra_depth more.
        If a line has no bullet, it becomes a bullet at the new depth.
        Empty lines are skipped.
        """
        nested_lines: list[str] = []

        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue

            leading_stars = len(line) - len(line.lstrip('*'))
            base_depth = leading_stars if leading_stars > 0 else 1  # ensure at least one bullet
            new_depth = base_depth + extra_depth

            # Remove existing stars and leading whitespace for the payload
            payload = line.lstrip('*').strip()
            nested_lines.append(f"{'*' * new_depth} {payload}")

        return nested_lines
    def generate_holy_sites_table(self):
        holy_sitess = self.parser.holy_sites.values()
        holy_sites_table_data = [{
            'Name': holy_sites.display_name,
            'Avatar': '' if holy_sites.avatar is None else holy_sites.avatar.get_wiki_link_with_icon() if holy_sites.avatar else '',  # avatar: <class 'eu5.eu5lib.Avatar'>
            'God': '' if holy_sites.god is None else holy_sites.god.get_wiki_link_with_icon() if holy_sites.god else '',  # god: <class 'eu5.eu5lib.God'>
            'Importance': holy_sites.importance,  # importance: <class 'int'>
            'Location': holy_sites.location.display_name if holy_sites.location else '',  # location: <class 'eu5.eu5lib.Location'>
            'Religions': self.create_wiki_list([religions.get_wiki_link_with_icon() if religions else '' for religions in holy_sites.religions]),  # religions: list[eu5.eu5lib.Religion]
            'Type': holy_sites.type.get_wiki_link_with_icon() if holy_sites.type else '',  # type: <class 'eu5.eu5lib.HolySiteType'>
        } for holy_sites in holy_sitess]
        return self.make_wiki_table(holy_sites_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_languages_table(self):
        languagess = self.parser.languages.values()
        languages_table_data = [{
            'Name': f' style="background-color: {languages.color.get_css_color_string() if languages.color else "white"}" | ' + languages.display_name,
            'Character Name Order': languages.character_name_order,  # character_name_order: <class 'str'>
            'Character Name Short Regnal Number': languages.character_name_short_regnal_number,  # character_name_short_regnal_number: <class 'str'>
            'Descendant Prefix': languages.descendant_prefix,  # descendant_prefix: <class 'str'>
            'Descendant Prefix Female': languages.descendant_prefix_female,  # descendant_prefix_female: <class 'str'>
            'Descendant Prefix Male': languages.descendant_prefix_male,  # descendant_prefix_male: <class 'str'>
            'Descendant Suffix': languages.descendant_suffix,  # descendant_suffix: <class 'str'>
            'Descendant Suffix Female': languages.descendant_suffix_female,  # descendant_suffix_female: <class 'str'>
            'Descendant Suffix Male': languages.descendant_suffix_male,  # descendant_suffix_male: <class 'str'>
            'Dialects': '' if languages.dialects is None else self.create_wiki_list([f'{k}: ...' for k in languages.dialects.keys()]) if languages.dialects else '',  # dialects: <class 'common.paradox_parser.Tree'>
            'Dynasty Names': self.create_wiki_list([dynasty_names for dynasty_names in languages.dynasty_names]),  # dynasty_names: list[str]
            'Dynasty Template Keys': self.create_wiki_list([dynasty_template_keys for dynasty_template_keys in languages.dynasty_template_keys]),  # dynasty_template_keys: list[str]
            'Fallback': languages.fallback if isinstance(languages.fallback, str) else '' if languages.fallback is None else languages.fallback.display_name if languages.fallback else '',  # fallback: <class 'eu5.eu5lib.Language'>
            'Family': '' if languages.family is None else languages.family.display_name if languages.family else '',  # family: <class 'eu5.eu5lib.LanguageFamily'>
            'Female Names': self.create_wiki_list([female_names for female_names in languages.female_names]),  # female_names: list[str]
            'First Name Conjoiner': languages.first_name_conjoiner,  # first_name_conjoiner: <class 'str'>
            'Location Prefix': languages.location_prefix,  # location_prefix: <class 'str'>
            'Location Prefix Ancient': languages.location_prefix_ancient,  # location_prefix_ancient: <class 'str'>
            'Location Prefix Ancient Vowel': languages.location_prefix_ancient_vowel,  # location_prefix_ancient_vowel: <class 'str'>
            'Location Prefix Elision': self.create_wiki_list([location_prefix_elision for location_prefix_elision in languages.location_prefix_elision]),  # location_prefix_elision: list[str]
            'Location Prefix Vowel': languages.location_prefix_vowel,  # location_prefix_vowel: <class 'str'>
            'Location Suffix': languages.location_suffix,  # location_suffix: <class 'str'>
            'Lowborn': self.create_wiki_list([lowborn for lowborn in languages.lowborn]),  # lowborn: list[str]
            'Male Names': self.create_wiki_list([male_names for male_names in languages.male_names]),  # male_names: list[str]
            'Patronym Prefix Daughter': languages.patronym_prefix_daughter,  # patronym_prefix_daughter: <class 'str'>
            'Patronym Prefix Daughter Vowel': languages.patronym_prefix_daughter_vowel,  # patronym_prefix_daughter_vowel: <class 'str'>
            'Patronym Prefix Son': languages.patronym_prefix_son,  # patronym_prefix_son: <class 'str'>
            'Patronym Prefix Son Vowel': languages.patronym_prefix_son_vowel,  # patronym_prefix_son_vowel: <class 'str'>
            'Patronym Suffix': languages.patronym_suffix,  # patronym_suffix: <class 'str'>
            'Patronym Suffix Daughter': languages.patronym_suffix_daughter,  # patronym_suffix_daughter: <class 'str'>
            'Patronym Suffix Son': languages.patronym_suffix_son,  # patronym_suffix_son: <class 'str'>
            'Require Genitive Location Names': '[[File:Yes.png|20px|Require Genitive Location Names]]' if languages.require_genitive_location_names else '[[File:No.png|20px|Not Require Genitive Location Names]]',  # require_genitive_location_names: <class 'bool'>
            'Ship Names': self.create_wiki_list([ship_names for ship_names in languages.ship_names]),  # ship_names: list[str]
        } for languages in languagess]
        return self.make_wiki_table(languages_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_levies_table(self):
        leviess = self.parser.levies.values()
        levies_table_data = [{
            'Name': levies.display_name,
            'Allow': self.formatter.format_trigger(levies.allow),  # allow: <class 'eu5.trigger.Trigger'>
            'Allow As Crew': self.formatter.format_trigger(levies.allow_as_crew),  # allow_as_crew: <class 'eu5.trigger.Trigger'>
            'Allowed Culture': self.create_wiki_list([allowed_culture.display_name if allowed_culture else '' for allowed_culture in levies.allowed_culture]),  # allowed_culture: list[eu5.eu5lib.Culture]
            'Allowed Pop Type': self.create_wiki_list([allowed_pop_type.get_wiki_link_with_icon() if allowed_pop_type else '' for allowed_pop_type in levies.allowed_pop_type]),  # allowed_pop_type: list[eu5.eu5lib.PopType]
            'Country Allow': self.formatter.format_trigger(levies.country_allow),  # country_allow: <class 'eu5.trigger.Trigger'>
            'Size': levies.size,  # size: <class 'float'>
            'Unit': levies.unit.get_wiki_link_with_icon() if levies.unit else '',  # unit: <class 'eu5.eu5lib.UnitType'>
        } for levies in leviess]
        return self.make_wiki_table(levies_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_parliament_agendas_table(self):
        parliament_agendass = self.parser.parliament_agendas.values()
        parliament_agendas_table_data = [{
            'Name': parliament_agendas.display_name,
            'Ai Will Do': '' if parliament_agendas.ai_will_do is None else parliament_agendas.ai_will_do.format() if hasattr(parliament_agendas.ai_will_do, 'format') else parliament_agendas.ai_will_do,  # ai_will_do: <class 'eu5.eu5lib.ScriptValue'>
            'Allow': self.formatter.format_trigger(parliament_agendas.allow),  # allow: <class 'eu5.trigger.Trigger'>
            'Can Bribe': self.formatter.format_trigger(parliament_agendas.can_bribe),  # can_bribe: <class 'eu5.trigger.Trigger'>
            'Chance': parliament_agendas.chance,  # chance: <class 'int'>
            'Estate': self.create_wiki_list([estate.get_wiki_link_with_icon() if estate else '' for estate in parliament_agendas.estate]),  # estate: list[eu5.eu5lib.Estate]
            'Importance': parliament_agendas.importance,  # importance: <class 'float'>
            'On Accept': self.formatter.format_effect(parliament_agendas.on_accept),  # on_accept: <class 'eu5.effect.Effect'>
            'On Bribe': self.formatter.format_effect(parliament_agendas.on_bribe),  # on_bribe: <class 'eu5.effect.Effect'>
            'Potential': self.formatter.format_trigger(parliament_agendas.potential),  # potential: <class 'eu5.trigger.Trigger'>
            'Special Status': '' if parliament_agendas.special_status is None else parliament_agendas.special_status,  # special_status: typing.Any
            'Type': parliament_agendas.type,  # type: <class 'str'>
        } for parliament_agendas in parliament_agendass]
        return self.make_wiki_table(parliament_agendas_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_peace_treaties_table(self):
        peace_treatiess = self.parser.peace_treaties.values()
        peace_treaties_table_data = [{
            'Name': f'{{{{iconbox|{peace_treaties.display_name}|{peace_treaties.description}|w=300px|image={peace_treaties.get_wiki_filename()}}}}}',
            'Ai Desire': '' if peace_treaties.ai_desire is None else peace_treaties.ai_desire.format() if hasattr(peace_treaties.ai_desire, 'format') else peace_treaties.ai_desire,  # ai_desire: <class 'eu5.eu5lib.ScriptValue'>
            'Allow': self.formatter.format_trigger(peace_treaties.allow),  # allow: <class 'eu5.trigger.Trigger'>
            'Antagonism Type': '' if peace_treaties.antagonism_type is None else peace_treaties.antagonism_type.display_name if peace_treaties.antagonism_type else '',  # antagonism_type: <class 'eu5.eu5lib.Bias'>
            'Are Targets Exclusive': '[[File:Yes.png|20px|Are Targets Exclusive]]' if peace_treaties.are_targets_exclusive else '[[File:No.png|20px|Not Are Targets Exclusive]]',  # are_targets_exclusive: <class 'bool'>
            'Base Antagonism': '' if peace_treaties.base_antagonism is None else peace_treaties.base_antagonism.format() if hasattr(peace_treaties.base_antagonism, 'format') else peace_treaties.base_antagonism,  # base_antagonism: <class 'eu5.eu5lib.ScriptValue'>
            'Blocks Full Annexation': '[[File:Yes.png|20px|Blocks Full Annexation]]' if peace_treaties.blocks_full_annexation else '[[File:No.png|20px|Not Blocks Full Annexation]]',  # blocks_full_annexation: <class 'bool'>
            'Category': peace_treaties.category,  # category: <class 'str'>
            'Cost': peace_treaties.cost.format() if hasattr(peace_treaties.cost, 'format') else peace_treaties.cost,  # cost: <class 'eu5.eu5lib.ScriptValue'>
            'Effect': self.formatter.format_effect(peace_treaties.effect),  # effect: <class 'eu5.effect.Effect'>
            'Potential': self.formatter.format_trigger(peace_treaties.potential),  # potential: <class 'eu5.trigger.Trigger'>
            'Select Trigger': '' if peace_treaties.select_trigger is None else self.create_wiki_list([f'{k}: ...' for k in peace_treaties.select_trigger.keys()]) if peace_treaties.select_trigger else '',  # select_trigger: <class 'common.paradox_parser.Tree'>
        } for peace_treaties in peace_treatiess]
        return self.make_wiki_table(peace_treaties_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )

    def get_sections(self, parser_attribute, groupby: str):
        sections = {}
        for category, traits in unsorted_groupby(entities, key=attrgetter(groupby)):
            traits = sorted(traits, key=attrgetter('display_name'))
            sections[f'traits_{category}'] = self.get_trait_table(traits)

    def generate_religions_table(self):
        religionss = self.parser.religions.values()
        religions_table_data = [{
            'Name': f' style="background-color: {religions.color.get_css_color_string() if religions.color else "white"}" | ' + f'{{{{iconbox|{religions.display_name}|{religions.description}|w=300px|image={religions.get_wiki_filename()}}}}}',
            'Ai Wants Convert': '[[File:Yes.png|20px|Ai Wants Convert]]' if religions.ai_wants_convert else '[[File:No.png|20px|Not Ai Wants Convert]]',  # ai_wants_convert: <class 'bool'>
            'Culture Locked': '[[File:Yes.png|20px|Culture Locked]]' if religions.culture_locked else '[[File:No.png|20px|Not Culture Locked]]',  # culture_locked: <class 'bool'>
            'Custom Tags': self.create_wiki_list([custom_tags for custom_tags in religions.custom_tags]),  # custom_tags: list[str]
            'Definition Modifier': self.format_modifier_section('definition_modifier', religions),  # definition_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Enable': religions.enable,  # enable: <class 'str'>
            'Factions': self.create_wiki_list([factions.get_wiki_link_with_icon() if factions else '' for factions in religions.factions]),  # factions: list[eu5.eu5lib.ReligiousFaction]
            'Group': '' if religions.group is None else religions.group.display_name if religions.group else '',  # group: <class 'eu5.eu5lib.ReligionGroup'>
            'Has Autocephalous Patriarchates': '[[File:Yes.png|20px|Has Autocephalous Patriarchates]]' if religions.has_autocephalous_patriarchates else '[[File:No.png|20px|Not Has Autocephalous Patriarchates]]',  # has_autocephalous_patriarchates: <class 'bool'>
            'Has Avatars': '[[File:Yes.png|20px|Has Avatars]]' if religions.has_avatars else '[[File:No.png|20px|Not Has Avatars]]',  # has_avatars: <class 'bool'>
            'Has Canonization': '[[File:Yes.png|20px|Has Canonization]]' if religions.has_canonization else '[[File:No.png|20px|Not Has Canonization]]',  # has_canonization: <class 'bool'>
            'Has Cardinals': '[[File:Yes.png|20px|Has Cardinals]]' if religions.has_cardinals else '[[File:No.png|20px|Not Has Cardinals]]',  # has_cardinals: <class 'bool'>
            'Has Doom': '[[File:Yes.png|20px|Has Doom]]' if religions.has_doom else '[[File:No.png|20px|Not Has Doom]]',  # has_doom: <class 'bool'>
            'Has Honor': '[[File:Yes.png|20px|Has Honor]]' if religions.has_honor else '[[File:No.png|20px|Not Has Honor]]',  # has_honor: <class 'bool'>
            'Has Karma': '[[File:Yes.png|20px|Has Karma]]' if religions.has_karma else '[[File:No.png|20px|Not Has Karma]]',  # has_karma: <class 'bool'>
            'Has Patriarchs': '[[File:Yes.png|20px|Has Patriarchs]]' if religions.has_patriarchs else '[[File:No.png|20px|Not Has Patriarchs]]',  # has_patriarchs: <class 'bool'>
            'Has Purity': '[[File:Yes.png|20px|Has Purity]]' if religions.has_purity else '[[File:No.png|20px|Not Has Purity]]',  # has_purity: <class 'bool'>
            'Has Religious Head': '[[File:Yes.png|20px|Has Religious Head]]' if religions.has_religious_head else '[[File:No.png|20px|Not Has Religious Head]]',  # has_religious_head: <class 'bool'>
            'Has Religious Influence': '[[File:Yes.png|20px|Has Religious Influence]]' if religions.has_religious_influence else '[[File:No.png|20px|Not Has Religious Influence]]',  # has_religious_influence: <class 'bool'>
            'Has Rite Power': '[[File:Yes.png|20px|Has Rite Power]]' if religions.has_rite_power else '[[File:No.png|20px|Not Has Rite Power]]',  # has_rite_power: <class 'bool'>
            'Has Yanantin': '[[File:Yes.png|20px|Has Yanantin]]' if religions.has_yanantin else '[[File:No.png|20px|Not Has Yanantin]]',  # has_yanantin: <class 'bool'>
            'Important Country': religions.important_country.get_wiki_link_with_icon() if religions.important_country else '',  # important_country: <class 'eu5.eu5lib.Country'>
            'Language': religions.language if isinstance(religions.language, str) else '' if religions.language is None else religions.language.display_name if religions.language else '',  # language: <class 'eu5.eu5lib.Language'>
            'Max Religious Figures For Religion': '' if religions.max_religious_figures_for_religion is None else religions.max_religious_figures_for_religion.format() if hasattr(religions.max_religious_figures_for_religion, 'format') else religions.max_religious_figures_for_religion,  # max_religious_figures_for_religion: <class 'eu5.eu5lib.ScriptValue'>
            'Max Sects': religions.max_sects,  # max_sects: <class 'int'>
            'Needs Reform': '[[File:Yes.png|20px|Needs Reform]]' if religions.needs_reform else '[[File:No.png|20px|Not Needs Reform]]',  # needs_reform: <class 'bool'>
            'Num Religious Focuses Needed For Reform': religions.num_religious_focuses_needed_for_reform,  # num_religious_focuses_needed_for_reform: <class 'int'>
            'Opinions': '' if religions.opinions is None else self.create_wiki_list([f'{k}: ...' for k in religions.opinions.keys()]) if religions.opinions else '',  # opinions: <class 'common.paradox_parser.Tree'>
            'Religious Aspects': religions.religious_aspects,  # religious_aspects: <class 'int'>
            'Religious Focuses': self.create_wiki_list([religious_focuses.get_wiki_link_with_icon() if religious_focuses else '' for religious_focuses in religions.religious_focuses]),  # religious_focuses: list[eu5.eu5lib.ReligiousFocus]
            '[RELIGIOUS_SCHOOL.GetName]': self.create_wiki_list([religious_school.get_wiki_link_with_icon() if religious_school else '' for religious_school in religions.religious_school]),  # religious_school: list[eu5.eu5lib.ReligiousSchool]
            'Tags': self.create_wiki_list([tags for tags in religions.tags]),  # tags: list[str]
            'Tithe': religions.tithe,  # tithe: <class 'float'>
            'Unique Names': self.create_wiki_list([unique_names for unique_names in religions.unique_names]),  # unique_names: list[str]
            'Use Icons': '[[File:Yes.png|20px|Use Icons]]' if religions.use_icons else '[[File:No.png|20px|Not Use Icons]]',  # use_icons: <class 'bool'>
        } for religions in religionss]
        return self.make_wiki_table(religions_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_religious_aspects_table(self):
        religious_aspectss = self.parser.religious_aspects.values()
        religious_aspects_table_data = [{
            'Name': f'{{{{iconbox|{religious_aspects.display_name}|{religious_aspects.description}|w=300px|image={religious_aspects.get_wiki_filename()}}}}}',
            'Modifier': self.format_modifier_section('modifier', religious_aspects),  # modifier: list[eu5.eu5lib.Eu5Modifier]
            'Enabled': self.formatter.format_trigger(religious_aspects.enabled),  # enabled: <class 'eu5.trigger.Trigger'>
            'Opinions': '' if religious_aspects.opinions is None else self.create_wiki_list([f'{k}: ...' for k in religious_aspects.opinions.keys()]) if religious_aspects.opinions else '',  # opinions: <class 'common.paradox_parser.Tree'>
            'Religion': self.create_wiki_list([religion.get_wiki_link_with_icon() if religion else '' for religion in religious_aspects.religion]),  # religion: list[eu5.eu5lib.Religion]
            'Visible': self.formatter.format_trigger(religious_aspects.visible),  # visible: <class 'eu5.trigger.Trigger'>
        } for religious_aspects in religious_aspectss]
        return self.make_wiki_table(religious_aspects_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_religious_schools_table(self):
        religious_schoolss = self.parser.religious_schools.values()
        religious_schools_table_data = [{
            'Name': f'{{{{iconbox|{religious_schools.display_name}|{religious_schools.description}|w=300px|image={religious_schools.get_wiki_filename()}}}}}',
            'Modifier': self.format_modifier_section('modifier', religious_schools),  # modifier: list[eu5.eu5lib.Eu5Modifier]
            'Enabled For Character': self.formatter.format_trigger(religious_schools.enabled_for_character),  # enabled_for_character: <class 'eu5.trigger.Trigger'>
            'Enabled For Country': self.formatter.format_trigger(religious_schools.enabled_for_country),  # enabled_for_country: <class 'eu5.trigger.Trigger'>
        } for religious_schools in religious_schoolss]
        return self.make_wiki_table(religious_schools_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_subject_types_table(self):
        subject_typess = self.parser.subject_types.values()
        subject_types_table_data = [{
            'Name': f' style="background-color: {subject_types.color.get_css_color_string() if subject_types.color else "white"}" | ' + f'{{{{iconbox|{subject_types.display_name}|{subject_types.description}|w=300px|image={subject_types.get_wiki_filename()}}}}}',
            'Ai Wants To Be Overlord': '' if subject_types.ai_wants_to_be_overlord is None else subject_types.ai_wants_to_be_overlord.format() if hasattr(subject_types.ai_wants_to_be_overlord, 'format') else subject_types.ai_wants_to_be_overlord,  # ai_wants_to_be_overlord: <class 'eu5.eu5lib.ScriptValue'>
            'Allow Declaring Wars': '[[File:Yes.png|20px|Allow Declaring Wars]]' if subject_types.allow_declaring_wars else '[[File:No.png|20px|Not Allow Declaring Wars]]',  # allow_declaring_wars: <class 'bool'>
            'Annexation Min Opinion': subject_types.annexation_min_opinion,  # annexation_min_opinion: <class 'int'>
            'Annexation Min Years Before': '' if subject_types.annexation_min_years_before is None else subject_types.annexation_min_years_before,  # annexation_min_years_before: <class 'int'>
            'Annexation Speed': subject_types.annexation_speed,  # annexation_speed: <class 'int'>
            'Annexation Stall Opinion': subject_types.annexation_stall_opinion,  # annexation_stall_opinion: <class 'int'>
            'Can Attack': self.formatter.format_trigger(subject_types.can_attack),  # can_attack: <class 'eu5.trigger.Trigger'>
            'Can Be Force Broken In Peace Treaty': '[[File:Yes.png|20px|Can Be Force Broken In Peace Treaty]]' if subject_types.can_be_force_broken_in_peace_treaty else '[[File:No.png|20px|Not Can Be Force Broken In Peace Treaty]]',  # can_be_force_broken_in_peace_treaty: <class 'bool'>
            'Can Change Heir Selection': '[[File:Yes.png|20px|Can Change Heir Selection]]' if subject_types.can_change_heir_selection else '[[File:No.png|20px|Not Can Change Heir Selection]]',  # can_change_heir_selection: <class 'bool'>
            'Can Change Rank': '[[File:Yes.png|20px|Can Change Rank]]' if subject_types.can_change_rank else '[[File:No.png|20px|Not Can Change Rank]]',  # can_change_rank: <class 'bool'>
            'Can Overlord Build Buildings': '[[File:Yes.png|20px|Can Overlord Build Buildings]]' if subject_types.can_overlord_build_buildings else '[[File:No.png|20px|Not Can Overlord Build Buildings]]',  # can_overlord_build_buildings: <class 'bool'>
            'Can Overlord Build Rgos': '[[File:Yes.png|20px|Can Overlord Build Rgos]]' if subject_types.can_overlord_build_rgos else '[[File:No.png|20px|Not Can Overlord Build Rgos]]',  # can_overlord_build_rgos: <class 'bool'>
            'Can Overlord Build Roads': '[[File:Yes.png|20px|Can Overlord Build Roads]]' if subject_types.can_overlord_build_roads else '[[File:No.png|20px|Not Can Overlord Build Roads]]',  # can_overlord_build_roads: <class 'bool'>
            'Can Overlord Build Ships': '[[File:Yes.png|20px|Can Overlord Build Ships]]' if subject_types.can_overlord_build_ships else '[[File:No.png|20px|Not Can Overlord Build Ships]]',  # can_overlord_build_ships: <class 'bool'>
            'Can Overlord Recruit Regiments': '[[File:Yes.png|20px|Can Overlord Recruit Regiments]]' if subject_types.can_overlord_recruit_regiments else '[[File:No.png|20px|Not Can Overlord Recruit Regiments]]',  # can_overlord_recruit_regiments: <class 'bool'>
            'Can Rival': self.formatter.format_trigger(subject_types.can_rival),  # can_rival: <class 'eu5.trigger.Trigger'>
            'Creation Visible': self.formatter.format_trigger(subject_types.creation_visible),  # creation_visible: <class 'eu5.trigger.Trigger'>
            'Diplo Chance Accept Overlord': '' if subject_types.diplo_chance_accept_overlord is None else self.create_wiki_list([f'{k}: ...' for k in subject_types.diplo_chance_accept_overlord.keys()]) if subject_types.diplo_chance_accept_overlord else '',  # diplo_chance_accept_overlord: <class 'common.paradox_parser.Tree'>
            'Diplo Chance Accept Subject': '' if subject_types.diplo_chance_accept_subject is None else self.create_wiki_list([f'{k}: ...' for k in subject_types.diplo_chance_accept_subject.keys()]) if subject_types.diplo_chance_accept_subject else '',  # diplo_chance_accept_subject: <class 'common.paradox_parser.Tree'>
            'Diplomatic Capacity Cost Scale': subject_types.diplomatic_capacity_cost_scale,  # diplomatic_capacity_cost_scale: <class 'float'>
            'Enabled Through Diplomacy': self.formatter.format_trigger(subject_types.enabled_through_diplomacy),  # enabled_through_diplomacy: <class 'eu5.trigger.Trigger'>
            'Fleet Basing Rights': '[[File:Yes.png|20px|Fleet Basing Rights]]' if subject_types.fleet_basing_rights else '[[File:No.png|20px|Not Fleet Basing Rights]]',  # fleet_basing_rights: <class 'bool'>
            'Food Access': '[[File:Yes.png|20px|Food Access]]' if subject_types.food_access else '[[File:No.png|20px|Not Food Access]]',  # food_access: <class 'bool'>
            'Government': '' if subject_types.government is None else subject_types.government.get_wiki_link_with_icon() if subject_types.government else '',  # government: <class 'eu5.eu5lib.GovernmentType'>
            'Great Power Score Transfer': subject_types.great_power_score_transfer,  # great_power_score_transfer: <class 'float'>
            'Has Limited Diplomacy': '[[File:Yes.png|20px|Has Limited Diplomacy]]' if subject_types.has_limited_diplomacy else '[[File:No.png|20px|Not Has Limited Diplomacy]]',  # has_limited_diplomacy: <class 'bool'>
            'Has Overlords Ruler': '[[File:Yes.png|20px|Has Overlords Ruler]]' if subject_types.has_overlords_ruler else '[[File:No.png|20px|Not Has Overlords Ruler]]',  # has_overlords_ruler: <class 'bool'>
            'Institution Spread To Overlord': subject_types.institution_spread_to_overlord.format() if hasattr(subject_types.institution_spread_to_overlord, 'format') else subject_types.institution_spread_to_overlord,  # institution_spread_to_overlord: <class 'eu5.eu5lib.ScriptValue'>
            'Institution Spread To Subject': subject_types.institution_spread_to_subject.format() if hasattr(subject_types.institution_spread_to_subject, 'format') else subject_types.institution_spread_to_subject,  # institution_spread_to_subject: <class 'eu5.eu5lib.ScriptValue'>
            'Is Colonial Subject': '[[File:Yes.png|20px|Is Colonial Subject]]' if subject_types.is_colonial_subject else '[[File:No.png|20px|Not Is Colonial Subject]]',  # is_colonial_subject: <class 'bool'>
            'Join Defensive Wars Always': self.formatter.format_trigger(subject_types.join_defensive_wars_always),  # join_defensive_wars_always: <class 'eu5.trigger.Trigger'>
            'Join Offensive Wars Always': self.formatter.format_trigger(subject_types.join_offensive_wars_always),  # join_offensive_wars_always: <class 'eu5.trigger.Trigger'>
            'Level': subject_types.level,  # level: <class 'int'>
            'Merchants To Overlord Fraction': subject_types.merchants_to_overlord_fraction,  # merchants_to_overlord_fraction: <class 'float'>
            'Minimum Opinion For Offer': subject_types.minimum_opinion_for_offer,  # minimum_opinion_for_offer: <class 'int'>
            'On Disable': self.formatter.format_effect(subject_types.on_disable),  # on_disable: <class 'eu5.effect.Effect'>
            'On Enable': self.formatter.format_effect(subject_types.on_enable),  # on_enable: <class 'eu5.effect.Effect'>
            'Only Overlord Court Language': '[[File:Yes.png|20px|Only Overlord Court Language]]' if subject_types.only_overlord_court_language else '[[File:No.png|20px|Not Only Overlord Court Language]]',  # only_overlord_court_language: <class 'bool'>
            'Only Overlord Culture': '[[File:Yes.png|20px|Only Overlord Culture]]' if subject_types.only_overlord_culture else '[[File:No.png|20px|Not Only Overlord Culture]]',  # only_overlord_culture: <class 'bool'>
            'Only Overlord Or Kindred Culture': '[[File:Yes.png|20px|Only Overlord Or Kindred Culture]]' if subject_types.only_overlord_or_kindred_culture else '[[File:No.png|20px|Not Only Overlord Or Kindred Culture]]',  # only_overlord_or_kindred_culture: <class 'bool'>
            'Overlord Can Cancel': '[[File:Yes.png|20px|Overlord Can Cancel]]' if subject_types.overlord_can_cancel else '[[File:No.png|20px|Not Overlord Can Cancel]]',  # overlord_can_cancel: <class 'bool'>
            'Overlord Can Enforce Peace On Subject': '[[File:Yes.png|20px|Overlord Can Enforce Peace On Subject]]' if subject_types.overlord_can_enforce_peace_on_subject else '[[File:No.png|20px|Not Overlord Can Enforce Peace On Subject]]',  # overlord_can_enforce_peace_on_subject: <class 'bool'>
            'Overlord Inherit If No Heir': '[[File:Yes.png|20px|Overlord Inherit If No Heir]]' if subject_types.overlord_inherit_if_no_heir else '[[File:No.png|20px|Not Overlord Inherit If No Heir]]',  # overlord_inherit_if_no_heir: <class 'bool'>
            'Overlord Modifier': self.format_modifier_section('overlord_modifier', subject_types),  # overlord_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Overlord Share Exploration': '[[File:Yes.png|20px|Overlord Share Exploration]]' if subject_types.overlord_share_exploration else '[[File:No.png|20px|Not Overlord Share Exploration]]',  # overlord_share_exploration: <class 'bool'>
            'Release Country Enabled': self.formatter.format_trigger(subject_types.release_country_enabled),  # release_country_enabled: <class 'eu5.trigger.Trigger'>
            'Strength Vs Overlord': subject_types.strength_vs_overlord,  # strength_vs_overlord: <class 'float'>
            'Subject Can Cancel': '' if subject_types.subject_can_cancel is None else '[[File:Yes.png|20px|Subject Can Cancel]]' if subject_types.subject_can_cancel else '[[File:No.png|20px|Not Subject Can Cancel]]',  # subject_can_cancel: <class 'bool'>
            'Subject Creation Enabled': self.formatter.format_trigger(subject_types.subject_creation_enabled),  # subject_creation_enabled: <class 'eu5.trigger.Trigger'>
            'Subject Modifier': self.format_modifier_section('subject_modifier', subject_types),  # subject_modifier: list[eu5.eu5lib.Eu5Modifier]
            'Subject Pays': subject_types.subject_pays.format(icon_only=True) if hasattr(subject_types.subject_pays, 'format') else subject_types.subject_pays,  # subject_pays: <class 'eu5.eu5lib.Price'>
            'Type': '' if subject_types.type is None else subject_types.type.display_name if subject_types.type else '',  # type: <class 'eu5.eu5lib.Eu5GameConcept'>
            'Use Overlord Laws': '[[File:Yes.png|20px|Use Overlord Laws]]' if subject_types.use_overlord_laws else '[[File:No.png|20px|Not Use Overlord Laws]]',  # use_overlord_laws: <class 'bool'>
            'Use Overlord Map Color': '' if subject_types.use_overlord_map_color is None else '[[File:Yes.png|20px|Use Overlord Map Color]]' if subject_types.use_overlord_map_color else '[[File:No.png|20px|Not Use Overlord Map Color]]',  # use_overlord_map_color: <class 'bool'>
            'Use Overlord Map Name': '[[File:Yes.png|20px|Use Overlord Map Name]]' if subject_types.use_overlord_map_name else '[[File:No.png|20px|Not Use Overlord Map Name]]',  # use_overlord_map_name: <class 'bool'>
            'Visible Through Diplomacy': self.formatter.format_trigger(subject_types.visible_through_diplomacy),  # visible_through_diplomacy: <class 'eu5.trigger.Trigger'>
            'Visible Through Treaty': self.formatter.format_trigger(subject_types.visible_through_treaty),  # visible_through_treaty: <class 'eu5.trigger.Trigger'>
            'War Score Cost': subject_types.war_score_cost,  # war_score_cost: <class 'float'>
        } for subject_types in subject_typess]
        return self.make_wiki_table(subject_types_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_traits_table(self):
        result = []
        for sectionname, section in self.get_trait_sections().items():
            result.append('')
            result.append(f'=== {sectionname} ===')
            result.append(self.surround_with_autogenerated_section(sectionname, section, add_version_header=True))
        return result
    def get_trait_sections(self):
        sections = {}
        for category, traits in unsorted_groupby(self.parser.traits.values(),
                                                   key=attrgetter('category')):
            traits = sorted(traits, key=attrgetter('display_name'))
            sections[f'traits_{category}'] = self.get_trait_table(traits)
        return sections

    def get_trait_table(self, traits):
        trait_table_data = [{
            'Name': f'{{{{iconbox|{trait.display_name}|{trait.description}|w=300px|image={trait.get_wiki_filename()}}}}}',
            'Modifier': self.format_modifier_section('modifier', trait),  # modifier: list[eu5.eu5lib.Eu5Modifier]
            'Requirements': self.formatter.format_trigger(trait.allow),  # allow: <class 'eu5.trigger.Trigger'>
            # 'Category': trait.category.display_name if trait.category else '',  # category: <class 'eu5.eu5lib.Eu5GameConcept'>
            'Chance': '' if trait.chance is None else self.create_wiki_list([f'{k}: ...' for k in trait.chance.keys()]) if trait.chance else '',  # chance: <class 'common.paradox_parser.Tree'>
            # 'Flavor': '' if trait.flavor is None else trait.flavor.display_name if trait.flavor else '',  # flavor: <class 'eu5.eu5lib.TraitFlavor'>
        } for trait in traits]
        return self.make_wiki_table(trait_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_unit_types_table(self):
        unit_typess = self.parser.unit_types.values()
        unit_types_table_data = [{
            'Name': f' style="background-color: {unit_types.color.get_css_color_string() if unit_types.color else "white"}" | ' + f'{{{{iconbox|{unit_types.display_name}|{unit_types.description}|w=300px|image={unit_types.get_wiki_filename()}}}}}',
            'Age': '' if unit_types.age is None else unit_types.age.get_wiki_link_with_icon() if unit_types.age else '',  # age: <class 'eu5.eu5lib.Age'>
            'Artillery Barrage': unit_types.artillery_barrage,  # artillery_barrage: <class 'int'>
            'Attrition Loss': unit_types.attrition_loss,  # attrition_loss: <class 'float'>
            'Blockade Capacity': unit_types.blockade_capacity,  # blockade_capacity: <class 'float'>
            'Bombard Efficiency': unit_types.bombard_efficiency,  # bombard_efficiency: <class 'float'>
            'Build Time Modifier': unit_types.build_time_modifier,  # build_time_modifier: <class 'float'>
            'Buildable': '' if unit_types.buildable is None else '[[File:Yes.png|20px|Buildable]]' if unit_types.buildable else '[[File:No.png|20px|Not Buildable]]',  # buildable: <class 'bool'>
            'Cannon': '' if unit_types.cannons is None else unit_types.cannons,  # cannons: <class 'int'>
            'Category': unit_types.category.get_wiki_link_with_icon() if unit_types.category else '',  # category: <class 'eu5.eu5lib.UnitCategory'>
            'Combat': '' if unit_types.combat is None else self.create_wiki_list([f'{k}: ...' for k in unit_types.combat.keys()]) if unit_types.combat else '',  # combat: <class 'common.paradox_parser.Tree'>
            'Combat Power': unit_types.combat_power,  # combat_power: <class 'float'>
            'Combat Speed': unit_types.combat_speed,  # combat_speed: <class 'float'>
            'Construction Demand': '' if unit_types.construction_demand is None else unit_types.construction_demand.format(icon_only=True) if hasattr(unit_types.construction_demand, 'format') else unit_types.construction_demand,  # construction_demand: <class 'eu5.eu5lib.GoodsDemand'>
            'Copy From': unit_types.copy_from.get_wiki_link_with_icon() if unit_types.copy_from else '',  # copy_from: <class 'eu5.eu5lib.UnitType'>
            'Country Potential': self.formatter.format_trigger(unit_types.country_potential),  # country_potential: <class 'eu5.trigger.Trigger'>
            'Crew Size': unit_types.crew_size,  # crew_size: <class 'float'>
            'Default': '[[File:Yes.png|20px|Default]]' if unit_types.default else '[[File:No.png|20px|Not Default]]',  # default: <class 'bool'>
            'Flanking Ability': unit_types.flanking_ability,  # flanking_ability: <class 'float'>
            'Food Consumption Per Strength': unit_types.food_consumption_per_strength,  # food_consumption_per_strength: <class 'float'>
            'Food Storage Per Strength': unit_types.food_storage_per_strength,  # food_storage_per_strength: <class 'float'>
            'Frontage': unit_types.frontage,  # frontage: <class 'float'>
            'Gfx Tags': '' if unit_types.gfx_tags is None else unit_types.gfx_tags,  # gfx_tags: typing.Any
            'Hull Size': unit_types.hull_size,  # hull_size: <class 'int'>
            'Impact': '' if unit_types.impact is None else self.create_wiki_list([f'{k}: ...' for k in unit_types.impact.keys()]) if unit_types.impact else '',  # impact: <class 'common.paradox_parser.Tree'>
            'Initiative': unit_types.initiative,  # initiative: <class 'float'>
            'Levy': '[[File:Yes.png|20px|Levy]]' if unit_types.levy else '[[File:No.png|20px|Not Levy]]',  # levy: <class 'bool'>
            'Light': '' if unit_types.light is None else unit_types.light,  # light: typing.Any
            'Limit': '' if unit_types.limit is None else unit_types.limit.format() if hasattr(unit_types.limit, 'format') else unit_types.limit,  # limit: <class 'eu5.eu5lib.ScriptValue'>
            'Location Potential': self.formatter.format_trigger(unit_types.location_potential),  # location_potential: <class 'eu5.trigger.Trigger'>
            'Location Trigger': self.formatter.format_trigger(unit_types.location_trigger),  # location_trigger: <class 'eu5.trigger.Trigger'>
            'Maintenance Demand': '' if unit_types.maintenance_demand is None else unit_types.maintenance_demand.format(icon_only=True) if hasattr(unit_types.maintenance_demand, 'format') else unit_types.maintenance_demand,  # maintenance_demand: <class 'eu5.eu5lib.GoodsDemand'>
            'Maritime Presence': '' if unit_types.maritime_presence is None else unit_types.maritime_presence.format() if hasattr(unit_types.maritime_presence, 'format') else unit_types.maritime_presence,  # maritime_presence: <class 'eu5.eu5lib.ScriptValue'>
            'Max Strength': unit_types.max_strength,  # max_strength: <class 'float'>
            'Mercenaries Per Location': self.create_wiki_list([f'{self.formatter.format_percent(factor)} {pop_type.get_wiki_icon()}' for m in unit_types.mercenaries_per_location for pop_type, factor in m.items()]),
            'Morale Damage Done': unit_types.morale_damage_done,  # morale_damage_done: <class 'float'>
            'Morale Damage Taken': unit_types.morale_damage_taken,  # morale_damage_taken: <class 'float'>
            'Movement Speed': unit_types.movement_speed,  # movement_speed: <class 'float'>
            'Strength Damage Done': unit_types.strength_damage_done,  # strength_damage_done: <class 'float'>
            'Strength Damage Taken': '' if unit_types.strength_damage_taken is None else unit_types.strength_damage_taken,  # strength_damage_taken: typing.Any
            'Supply Weight': unit_types.supply_weight,  # supply_weight: <class 'float'>
            'Transport Capacity': unit_types.transport_capacity,  # transport_capacity: <class 'float'>
            'Upgrades To': '' if unit_types.upgrades_to is None else unit_types.upgrades_to,  # upgrades_to: typing.Any
            'Upgrades To Only': unit_types.upgrades_to_only.get_wiki_link_with_icon() if unit_types.upgrades_to_only else '',  # upgrades_to_only: <class 'eu5.eu5lib.UnitType'>
            'Use Ship Names': '' if unit_types.use_ship_names is None else '[[File:Yes.png|20px|Use Ship Names]]' if unit_types.use_ship_names else '[[File:No.png|20px|Not Use Ship Names]]',  # use_ship_names: <class 'bool'>
        } for unit_types in unit_typess]
        return self.make_wiki_table(unit_types_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )
    def generate_wargoals_table(self):
        wargoalss = self.parser.wargoals.values()
        wargoals_table_data = [{
            'Name': f'{{{{iconbox|{wargoals.display_name}|{wargoals.description}|w=300px|image={wargoals.get_wiki_filename()}}}}}',
            'Attacker': '' if wargoals.attacker is None else self.create_wiki_list([f'{k}: ...' for k in wargoals.attacker.keys()]) if wargoals.attacker else '',  # attacker: <class 'common.paradox_parser.Tree'>
            'Defender': '' if wargoals.defender is None else self.create_wiki_list([f'{k}: ...' for k in wargoals.defender.keys()]) if isinstance(wargoals.defender, Tree) else "''Unknown type''" if wargoals.defender else '',  # defender: <class 'common.paradox_parser.Tree'>
            'Ticking War Score': wargoals.ticking_war_score,  # ticking_war_score: <class 'float'>
            'Type': wargoals.type,  # type: <class 'str'>
            'War Name': wargoals.war_name,  # war_name: <class 'str'>
            'War Name Is Country Order Agnostic': '[[File:Yes.png|20px|War Name Is Country Order Agnostic]]' if wargoals.war_name_is_country_order_agnostic else '[[File:No.png|20px|Not War Name Is Country Order Agnostic]]',  # war_name_is_country_order_agnostic: <class 'bool'>
        } for wargoals in wargoalss]
        return self.make_wiki_table(wargoals_table_data, table_classes=['mildtable', 'plainlist'],
                                        one_line_per_cell=True,
                                        remove_empty_columns=True,
                                        )

    def generate_country_count(self):
        locations = self.parser.locations
        count = {
            'province': {},
            'area': {},
            'region': {},
            'sub_continent': {},
            'continent': {},
        }
        countries = self.parser.countries.values()
        for country in countries:
            if not country.capital:
                #print(country)
                continue
            capital = country.capital
            if isinstance(capital, str) and capital in locations:
                capital = locations[capital]
            elif isinstance(capital, list) and capital[0] in locations:
                capital = locations[capital[0]]
            
            if capital.province:
                if capital.province.display_name not in count['province']: count['province'][capital.province.display_name] = 0
                count['province'][capital.province.display_name] += 1
            if capital.area:
                if capital.area.display_name not in count['area']: count['area'][capital.area.display_name] = 0
                count['area'][capital.area.display_name] += 1
            if capital.region:
                if capital.region.display_name not in count['region']: count['region'][capital.region.display_name] = 0
                count['region'][capital.region.display_name] += 1
            if capital.sub_continent:
                if capital.sub_continent.display_name not in count['sub_continent']: count['sub_continent'][capital.sub_continent.display_name] = 0
                count['sub_continent'][capital.sub_continent.display_name] += 1
            if capital.continent:
                if capital.continent.display_name not in count['continent']: count['continent'][capital.continent.display_name] = 0
                count['continent'][capital.continent.display_name] += 1
        print(count)
        table = {}
        result = ''
        for ctype in count.keys():
            #print(ctype)
            #continue
            table = [{
                ctype.capitalize(): name,
                'Count': count[ctype][name]
            } for name in count[ctype]]
            #print(table)
            result += self.make_wiki_table(table) 
        return result
    
if __name__ == '__main__':
    TableGenerator().run(sys.argv)