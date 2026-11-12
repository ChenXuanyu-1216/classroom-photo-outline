import importlib.util
import json
from pathlib import Path
import pytest


def evaluator():
    path = Path(__file__).resolve().parents[2] / 'scripts/content_regressions.py'
    spec = importlib.util.spec_from_file_location('content_regressions', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrong_parent_with_all_words_present_fails_relationship_gate():
    case = {'required_edges': [['CART', '分类和回归树']],
            'forbidden_edges': [['人工神经网络', '分类和回归树']]}
    wrong = {'relations': [{'parent': '人工神经网络', 'children': ['分类和回归树']},
                           {'parent': '决策树', 'children': ['CART']}], 'flows': []}
    result = evaluator().diagram_checks(wrong, case)
    assert result['passed'] is False
    assert result['missing_edges'] == [['CART', '分类和回归树']]
    assert result['forbidden_edges_found'] == [['人工神经网络', '分类和回归树']]


def test_flow_missing_first_stage_or_inserting_container_fails_order_gate():
    expected = ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']
    case = {'required_flow': expected}
    for wrong in [expected[1:], ['业务需求', '数据仓库'] + expected[1:], expected[::-1]]:
        result = evaluator().diagram_checks({'flows': [{'name': '主流程', 'steps': wrong}]}, case)
        assert not result['passed']
        assert not result['flow_matches']
    assert evaluator().diagram_checks({'flows': [{'name': '主流程', 'steps': expected}]}, case)['passed']


def test_explicit_allowed_feedback_does_not_replace_or_corrupt_main_flow():
    chain = ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']
    feedback = ['部署评估', '特征工程']
    case = {'required_flow': chain, 'allowed_feedback_flows': [feedback]}
    main = {'name': '主流程', 'steps': chain}
    loop = {'name': '反馈环（虚线）', 'steps': feedback}
    module = evaluator()
    assert module.diagram_checks({'flows': [main, loop]}, case)['passed']
    assert not module.diagram_checks({'flows': [loop]}, case)['passed']
    assert not module.diagram_checks({'flows': [main, {'name': '反馈环', 'steps': chain[::-1]}]}, case)['passed']
    assert not module.diagram_checks({'flows': [main, {'name': '另一主流程', 'steps': feedback}]}, case)['passed']


def test_missing_case_is_unscored_not_success():
    assert evaluator().diagram_checks({}, {'required_edges': [['CART', '分类和回归树']]})['passed'] is False


def test_v_model_generalization_and_untriggered_case_do_not_pass():
    bad = [{'content': 'v-model 在表单控件上都等价于 value 属性和 input 事件。', 'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('vue_controls', bad)['passed']
    assert not evaluator().expansion_checks('vue_controls', [])['passed']
    good = [{'content': 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。',
             'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('vue_controls', good)['passed']
    good[0]['source_type'] = '课堂原话'
    assert not evaluator().expansion_checks('vue_controls', good)['passed']


def test_non_probability_model_can_be_evaluated_from_labels():
    bad = [{'content': '非概率模型常只能依据决策边界或排序指标。', 'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('label_metrics', bad)['passed']
    good = [{'content': '真实类别和预测类别即可计算准确率、精确率、召回率、F1，不需要概率。ROC-AUC 可用决策分数；对数损失需要概率。',
             'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('label_metrics', good)['passed']


def test_correction_cannot_hide_old_inaccurate_point_elsewhere():
    points = [{'content': '非概率模型常只能依据决策边界或排序指标。', 'source_type': 'AI生成'},
              {'content': '真实类别和预测类别即可计算准确率、精确率、召回率、F1，不需要概率。ROC-AUC 可用决策分数；对数损失需要概率。',
               'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('label_metrics', points)['passed']


def test_all_tokens_with_wrong_control_bindings_still_fails():
    wrong = [{'content': 'v-model 文本 input/textarea 使用 checked/change；checkbox/radio 使用 value/input；select 使用 checked/input。',
              'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('vue_controls', wrong)['passed']


def test_negated_bad_claim_is_a_correction_not_an_error():
    corrected = [{'content': 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。不能认为所有控件都是 value/input。',
                  'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('vue_controls', corrected)['passed']
    corrected[0]['content'] = corrected[0]['content'].replace('不能认为所有控件都是', '不能统一当作')
    assert evaluator().expansion_checks('vue_controls', corrected)['passed']
    corrected[0]['content'] = corrected[0]['content'].replace('不能统一当作 value/input', '统一按 value/input 处理会出错')
    assert evaluator().expansion_checks('vue_controls', corrected)['passed']


def test_extra_conflicting_flow_and_bad_parent_point_cannot_hide_beside_correction():
    chain = ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']
    assert not evaluator().diagram_checks({'flows': [{'steps': chain}, {'steps': ['业务需求']+chain[1:]}]},
                                           {'required_flow': chain})['passed']
    case = {'required_edges': [['CART', '分类和回归树']],
            'forbidden_edges': [['人工神经网络', '分类和回归树']]}
    wrong = {'relations': [{'parent': 'CART', 'children': ['分类和回归树']}],
             'points': [{'content': '人工神经网络包含分类和回归树', 'source_type': '课堂原话'}]}
    assert not evaluator().diagram_checks(wrong, case)['passed']
    wrong_flow_point = {'flows': [{'steps': chain}], 'points': [{
        'content': '主流程：业务需求 → 数据仓库 → 数据清洗 → 特征工程 → 数据建模 → 部署评估'}]}
    assert not evaluator().diagram_checks(wrong_flow_point, {'required_flow': chain})['passed']


def test_named_common_misconception_followed_by_correction_is_not_an_assertion():
    points = [{'content': '分类评价：真实类别与预测类别可以计算准确率、精确率、召回率、F1，不要求概率。ROC-AUC 需要分数，对数损失需要概率。常见误区是认为非概率模型只能评价边界；实际上标签即可评价。',
               'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('label_metrics', points)['passed']


def test_only_labels_needed_is_equivalent_to_not_requiring_probability():
    points = [{'content': '准确率、精确率、召回率、F1 只需真实类别和预测类别；ROC-AUC 需要分数；对数损失和校准才要求概率。',
               'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('label_metrics', points)['passed']
    points[0]['content'] = '准确率、精确率、召回率、F1 只需要真实标签加预测标签；ROC-AUC 需要分数；对数损失和校准才要求概率。类别标签足够计算前一组指标。'
    assert evaluator().expansion_checks('label_metrics', points)['passed']


def test_label_based_metric_explanation_does_not_need_a_magic_negation_phrase():
    points = [{'content':
        '分类评价指标建立在类别标签之上：真实类别与预测类别可构成混淆矩阵，准确率是整体判对比例；'
        '精确率反映预测为正的样本中真正为正的比例，召回率反映真实正例中被找出的比例，F1 是二者调和平均。'
        '二分类 ROC-AUC 可使用决策分数或概率。对数损失需要合法的概率；未校准的概率仍可计算对数损失。',
        'source_type': 'AI生成'}]
    result = evaluator().expansion_checks('label_metrics', points)
    assert result['passed']
    assert result['scoped_checks']['labels_without_probability']


def test_targeted_recheck_rejects_incomplete_run_and_changed_reference(tmp_path):
    module = evaluator()
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'status': 'started'}), encoding='utf-8')
    with pytest.raises(ValueError, match='未结束'):
        module.check_targeted_run(tmp_path)
    report.write_text(json.dumps({'status': 'completed', 'cases_sha256': 'changed'}), encoding='utf-8')
    with pytest.raises(ValueError, match='反例'):
        module.check_targeted_run(tmp_path)


def test_correct_label_metrics_cannot_hide_wrong_logistic_regression_taxonomy():
    correct = '真实类别和预测类别即可计算准确率、精确率、召回率、F1，不要求概率；ROC-AUC 要分数，对数损失要概率。'
    wrong = [{'content': correct, 'source_type': 'AI生成'}, {'content':
        '监督学习分为概率模型、非概率判别模型与非概率非参数模型。非概率判别模型如逻辑回归虽建模概率。', 'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('label_metrics', wrong)['passed']


def test_correct_binding_cannot_hide_asynchronous_model_assignment_claim():
    correct = 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。'
    wrong = [{'content': correct, 'source_type': 'AI生成'}, {'content':
        'v-model 的数据更新不是同步立即生效，事件回调后读取可能还是旧值。', 'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('vue_controls', wrong)['passed']


@pytest.mark.parametrize('wrong_mapping', ['radio 绑定 value 与 change', 'radio绑定 value 与 change'])
def test_later_correct_checked_mapping_cannot_hide_radio_value_change_error(wrong_mapping):
    correct = 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。'
    wrong = [{'content': wrong_mapping + '，选中时把该 radio 的 value 写入模型。', 'source_type': 'AI生成'},
             {'content': correct, 'source_type': 'AI生成'}]
    assert not evaluator().expansion_checks('vue_controls', wrong)['passed']
    legitimate_value = [{'content': correct + 'radio 的 value 是选项值，change 时将选中选项的 value 写入模型。',
                         'source_type': 'AI生成'}]
    assert evaluator().expansion_checks('vue_controls', legitimate_value)['passed']


def test_misconception_label_applies_to_its_whole_clause_not_previous_sentences():
    correct = 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。'
    warning = '常见误区：把所有表单控件的 v-model 都理解为 value/input，或者把自定义组件的 v-model 约定与原生控件混为一谈。'
    assert evaluator().expansion_checks('vue_controls', [{'content': correct + warning, 'source_type': 'AI生成'}])['passed']
    wrong = correct + '所有表单控件都使用 value/input。' + warning
    assert not evaluator().expansion_checks('vue_controls', [{'content': wrong, 'source_type': 'AI生成'}])['passed']


@pytest.mark.parametrize('warning', [
    '不要笼统地说 v-model 数据更新是异步的。',
    '事件触发时机、事件发生后的模型赋值、DOM 刷新是三个不同层面，不能笼统概括为 v-model 数据更新是异步的。',
])
def test_do_not_say_async_warning_is_not_an_async_assignment_claim(warning):
    correct = 'v-model：文本 input/textarea 使用 value/input；checkbox/radio 使用 checked/change；select 使用 value/change。'
    module = evaluator()
    assert module.expansion_checks('vue_controls', [{'content': correct + warning, 'source_type': 'AI生成'}])['passed']
    # A later warning must not erase an earlier affirmative error.
    wrong = correct + 'v-model 数据更新是异步的。' + warning
    assert not module.expansion_checks('vue_controls', [{'content': wrong, 'source_type': 'AI生成'}])['passed']
    # A warning in the same sentence also cannot cancel an earlier false claim.
    wrong = correct + 'v-model 数据更新是异步的，' + warning
    assert not module.expansion_checks('vue_controls', [{'content': wrong, 'source_type': 'AI生成'}])['passed']


@pytest.mark.parametrize('claim', [
    '对数损失和概率校准则要求输出经过校准的概率。',
    '对数损失必须使用已校准概率。',
    '对数损失和概率校准才要求输出概率。',
])
def test_log_loss_and_calibration_input_misconceptions_fail_even_with_correct_labels(claim):
    correct = '真实类别和预测类别即可计算准确率、精确率、召回率、F1，不要求概率；二分类 ROC-AUC 可用决策分数。'
    assert not evaluator().expansion_checks('label_metrics', [
        {'content': correct + claim, 'source_type': 'AI生成'}])['passed']


def test_uncalibrated_probability_and_raw_score_calibration_correction_passes():
    correct = ('真实类别和预测类别即可计算准确率、精确率、召回率、F1，不要求概率；'
               '二分类 ROC-AUC 可用决策分数。对数损失需要概率预测，但不要求已校准。'
               '不能认为对数损失必须使用已校准概率。概率校准可将原始决策分数映射为概率。')
    assert evaluator().expansion_checks('label_metrics', [{'content': correct, 'source_type': 'AI生成'}])['passed']


@pytest.mark.parametrize('claim', [
    '对数损失不要求已校准概率。',
    '对数损失不需要经过校准的概率。',
    '对数损失需要概率。概率校准需要输出概率，但可输入原始决策分数。',
])
def test_negated_calibration_prerequisite_and_calibrator_output_are_not_errors(claim):
    correct = '真实类别和预测类别即可计算准确率、精确率、召回率、F1，不要求概率；二分类 ROC-AUC 可用决策分数。'
    assert evaluator().expansion_checks('label_metrics', [
        {'content': correct + claim, 'source_type': 'AI生成'}])['passed']


@pytest.mark.parametrize('attribution', ['relation', 'point'])
def test_flow_notes_without_visible_owner_cannot_be_assigned_to_stage(attribution):
    chain = ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']
    note = '对最佳模型执行超参数调整'
    case = {'required_flow': chain, 'required_standalone_notes': [note]}
    observed = {'flows': [{'steps': chain}], 'points': [{'content': note, 'source_type': '课堂原话'}]}
    if attribution == 'relation':
        observed['relations'] = [{'parent': '部署评估', 'children': [note]}]
    else:
        observed['points'][0]['content'] = '数据建模：\n' + note
    result = evaluator().diagram_checks(observed, case)
    assert not result['passed']
    assert result['unsupported_note_attribution']


def test_flow_notes_are_preserved_with_uncertain_owner_instead_of_dropped():
    chain = ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']
    note = '对最佳模型执行超参数调整'
    case = {'required_flow': chain, 'required_standalone_notes': [note]}
    observed = {'flows': [{'steps': chain}], 'points': []}
    assert not evaluator().diagram_checks(observed, case)['passed']
    observed['points'] = [{'content': note, 'source_type': '课堂原话'}]
    assert evaluator().diagram_checks(observed, case)['passed']
    observed['points'] = [{'content': '数据建模：' + note + '（归属待核对）', 'source_type': '来源待核对'}]
    assert evaluator().diagram_checks(observed, case)['passed']
