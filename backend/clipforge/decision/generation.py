"""Versioned candidate generation settings, captured before processing/cache lookup."""

from copy import deepcopy
from dataclasses import asdict
import os

from clipforge.decision.candidates import WindowOptions, generate_candidates
from clipforge.decision.topic_segments import TopicOptions, generate_topic_candidates

LEGACY = 'sentence-window-v1'
TOPIC = 'topic-valley-v1'


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != {'version', 'options'}:
        raise ValueError('invalid candidate generation settings')
    if not isinstance(settings['version'], str):
        raise ValueError('candidate generation version must be a string')
    options_type = {LEGACY: WindowOptions, TOPIC: TopicOptions}.get(settings['version'])
    if options_type is None or not isinstance(settings['options'], dict):
        raise ValueError('unknown candidate generation strategy')
    if set(settings['options']) != set(asdict(options_type())):
        raise ValueError('candidate generation options must be explicit')
    try:
        return options_type(**settings['options'])
    except TypeError as error:
        raise ValueError('invalid candidate generation options') from error


def settings_snapshot():
    # Fixed v1 defaults; no footage-specific thresholds or secret/model settings.
    # Topic segmentation stays opt-in until an independent quality evaluation.
    # Existing successful tasks and the production default must remain usable.
    version = os.environ.get('CLIPFORGE_CANDIDATE_GENERATOR', LEGACY)
    options_type = {LEGACY: WindowOptions, TOPIC: TopicOptions}.get(version)
    if options_type is None:
        raise ValueError('unknown CLIPFORGE_CANDIDATE_GENERATOR')
    return {'version': version, 'options': asdict(options_type())}


def generate_configured_candidates(analysis, *, settings=None):
    settings = settings_snapshot() if settings is None else deepcopy(settings)
    options = validate_settings(settings)
    generator = generate_topic_candidates if settings['version'] == TOPIC else generate_candidates
    return generator(analysis, options)


def effective_boundary_settings(settings, generation):
    validate_settings(generation)
    # Sentence repair would undo whole-topic ranges. Keep it available only for
    # the explicit legacy baseline, never silently apply it to topic candidates.
    return {'mode': 'off'} if generation['version'] == TOPIC else deepcopy(settings)
