"""Ground the native-radio fact in a reviewed reference, not free-form prose.

This is a deliberately narrow reference rule, not a general fact checker.
Reviewed against Vue's Form Input Bindings on 2026-10-22. Revisit the card
when that contract changes. No runtime web request or extra model call.
"""
import re

from app.services.model_output import ExpandedPoint


NATIVE_RADIO_REFERENCE = (
    'Vue 原生表单 v-model 的属性/事件映射：文本 input/textarea 默认使用 value/input；'
    'checkbox 使用 checked/change；radio 使用 checked/change；select 使用 value/change。'
    'radio 的 checked 由模型值与该选项的 value 是否匹配决定；change 时将选中选项的 value 写入模型。'
    '例如 value="a" 的 radio 被选中后，模型值为 "a"。value 是选项值，checked 是选中状态，两者不能互换。'
    '文本输入使用 .lazy 时改为 change。以上映射适用于原生元素，自定义组件需遵循其组件的 v-model 协议。'
    '事件处理中的响应式模型赋值是同步的，DOM 更新由 Vue 批量延后；如需读取更新后的 DOM，可在赋值后 await nextTick()。'
    '本条依据 Vue 官方表单绑定文档整理：https://vuejs.org/guide/essentials/forms.html#basic-usage'
    '；https://vuejs.org/guide/essentials/reactivity-fundamentals.html#dom-update-timing'
)

NATIVE_FORM_DETAILS = (
    '原生表单的模型形态与修饰符：单选 select 绑定单值，多选 select 绑定数组；'
    'option 没有显式 value 时可使用其文本作为值，不能要求所有选择控件必须显式设置 value。'
    '单个 checkbox 通常使用布尔模型，也可配置 true-value/false-value；复选框组可使用数组，Vue 3 也支持 Set。'
    '文本输入默认在 input 时同步模型，.lazy 改为 change，因此文本输入监听的事件发生改变；'
    'checkbox/radio 与 select 本来就使用 change，不能把 .lazy 概括为所有控件统一切换事件。'
    '.trim 用于去除输入首尾空白。这些规则属于原生元素，不能直接套用到自定义组件。'
    '依据 Vue 官方表单文档整理：https://vuejs.org/guide/essentials/forms.html'
)

COMPONENT_MODEL_REFERENCE = (
    'Vue 组件 v-model 需区分版本与参数：Vue 3 的默认协议为 modelValue prop 与 update:modelValue 事件；'
    '带参数 v-model:title 对应 title prop 与 update:title 事件，而非沿用默认名称。'
    'Vue 3.4 起可用 defineModel() 表达默认绑定，用 defineModel("title") 表达同名参数绑定。'
    'Vue 2 默认使用 value/input，可通过组件 model 选项配置 prop 与 event；不要把 Vue 2 的 model 选项套到 Vue 3。'
    '这些组件协议与原生表单的属性/事件映射需分别理解。'
    '本条依据 Vue 官方组件文档整理：https://vuejs.org/guide/components/v-model.html#v-model-arguments'
)


def _native_radio_paragraph(content):
    # Python's Unicode \b treats adjacent Chinese as part of the identifier.
    # ASCII identifier boundaries accept "radio绑定" but exclude "radio-picker".
    radio = r'(?<![a-z0-9_-])radio(?![a-z0-9_-])|单选(?:按钮|框)?'
    if not re.search(radio, content, re.IGNORECASE):
        return False
    component_pattern = r'组件|custom component|modelvalue|definemodel'
    component = re.search(component_pattern, content, re.IGNORECASE)
    if not component:
        return True
    # A separate native-radio clause still needs grounding even if a later
    # clause discusses a component. Component-only paragraphs remain intact.
    return any(re.search(radio, clause, re.IGNORECASE)
               and not re.search(component_pattern, clause, re.IGNORECASE)
               for clause in re.split(r'[。；;，,\n]', content))


def _native_form_context(anchors, page_titles):
    # A broad page title cannot turn a component-only point into a native
    # element contract. Comparisons explicitly mentioning native elements may
    # still qualify.
    anchors = [anchor for anchor in anchors
               if not (re.search(r'自定义组件|custom component', anchor.content, re.IGNORECASE)
                       and not re.search(r'原生|native', anchor.content, re.IGNORECASE))]
    context = ' '.join(
        anchor.content + ' ' + ' '.join(page_titles.get(int(page[1:]), '')
                                        for page in anchor.source_pages.split(','))
        for anchor in anchors
    ).lower()
    return ('vue' in context and re.search(r'v\s*-\s*model', context)
            and re.search(r'原生|表单|<input\b|input/textarea', context))


def ground_native_radio_points(points, point_map, page_titles):
    """Caller must validate *all* model references before applying this rule.

    Consolidate radio paragraphs only in their own native-form source context.
    Replace the whole paragraph so a later correct clause cannot hide an earlier
    contradiction. Keep other additions and merge the real classroom anchors.
    The caller still owns AI labels and page provenance.
    """
    result: list[ExpandedPoint] = []
    grounded = None
    for point in points:
        anchors = [point_map[point_id] for point_id in point.based_on_ids]
        if (not _native_radio_paragraph(point.content)
                or not _native_form_context(anchors, page_titles)):
            result.append(point)
            continue
        if grounded is None:
            grounded = point.model_copy(update={
                'content': NATIVE_RADIO_REFERENCE, 'kp_type': 'concept',
                'based_on_ids': list(point.based_on_ids),
            })
            result.append(grounded)
        else:
            grounded.based_on_ids.extend(point_id for point_id in point.based_on_ids
                                         if point_id not in grounded.based_on_ids)
    return result


def ground_component_model_points(points, point_map):
    """Consolidate named/default protocol comparisons in their Vue context."""
    result = []
    grounded = None
    for point in points:
        ids = [point_id for point_id in point.based_on_ids
               if 'vue' in point_map[point_id].content.lower()
               and re.search(r'v\s*-\s*model', point_map[point_id].content, re.IGNORECASE)]
        named = re.search(r'v-model\s*[:：]|v-model\s*(?:的)?\s*(?:参数|具名|命名)|'
                          r'(?:带参数|具名|命名)\s*(?:的)?\s*v-model|defineModel\s*\(\s*[\'\"]',
                          point.content, re.IGNORECASE)
        protocol = re.search(r'modelvalue|update:modelvalue', point.content, re.IGNORECASE)
        if not ids or not named or not protocol:
            result.append(point)
            continue
        if grounded is None:
            grounded = point.model_copy(update={'content': COMPONENT_MODEL_REFERENCE,
                                                 'kp_type': 'concept', 'based_on_ids': ids})
            result.append(grounded)
        else:
            grounded.based_on_ids.extend(item for item in ids if item not in grounded.based_on_ids)
    return result


def ground_native_form_edge_cases(points, point_map, page_titles):
    """Correct two observed assertions in their own native-form context only."""
    result, grounded = [], None
    for point in points:
        ids = [point_id for point_id in point.based_on_ids
               if _native_form_context([point_map[point_id]], page_titles)]
        lazy_error = re.search(r'\.lazy[\s\S]*(?:不|未)(?:会)?改变[^。；;\n]{0,20}事件', point.content, re.I)
        value_required = (re.search(r'select|option|选择类控件', point.content, re.I)
                          and re.search(r'(?:必须|一定|务必)[^。；;\n]{0,20}(?:设置|指定)\s*value', point.content, re.I))
        if not ids or not (lazy_error or value_required):
            result.append(point)
        elif grounded is None:
            grounded = point.model_copy(update={'content': NATIVE_FORM_DETAILS,
                                                 'kp_type': 'concept', 'based_on_ids': ids})
            result.append(grounded)
        else:
            grounded.based_on_ids.extend(item for item in ids if item not in grounded.based_on_ids)
    return result
