"""Explicit local voice reference preparation, selection and observation only."""
import argparse
import json
from pathlib import Path
import re
from .acquire import save_json, video_number
from .local_files import plain_path, read_json
from .subtitle_enhancement.profile import (
    ENCODER_ID, VOICE_SCHEMA, contained_path, sha256_file, valid_identity,
    validate_voice_manifest, resolve_profile,
)
from .subtitle_enhancement.reference import ERes2ReferenceEncoder, read_pcm16, observe_reference

ROOT = Path(__file__).resolve().parents[1]

def identity(channel, revision=None):
    if not valid_identity('chzzk', channel):
        raise ValueError('An exact CHZZK channel ID is required')
    if revision is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', revision):
        raise ValueError('Invalid profile revision')

def prepare_profile(root, spec, encoder_factory=ERes2ReferenceEncoder):
    """Spec paths are relative to this project's private .voice folder."""
    import numpy as np
    root = plain_path(root)
    channel, revision = spec['channel_id'], spec['revision']
    identity(channel, revision)
    if spec.get('recording_rights_confirmed') is not True or spec.get('model_terms_confirmed') is not True:
        raise ValueError('Explicit recording rights and external model terms confirmation required')
    for key in ('model_source_url', 'model_revision', 'model_license'):
        if not isinstance(spec.get(key), str) or not spec[key].strip() or len(spec[key]) > 2000:
            raise ValueError('Missing external model provenance: ' + key)
    if not spec['model_source_url'].startswith('https://'):
        raise ValueError('Record the official HTTPS model source')
    model = contained_path(root, spec['encoder_path'])
    model_hash = sha256_file(model)
    if model_hash != spec['encoder_sha256']:
        raise ValueError('Encoder checksum mismatch')
    recordings = spec['recordings']
    if not isinstance(recordings, list) or not 1 <= len(recordings) <= 64:
        raise ValueError('Supply 1-64 authorized recording paths')
    audio = [read_pcm16(contained_path(root, item)) for item in recordings]
    directory = plain_path(root/'chzzk'/channel/revision)
    if directory.exists():
        raise ValueError('Profile revision already exists; choose a new revision')
    encoder = encoder_factory(model, model_hash)
    vectors = np.stack([encoder.embed(item) for item in audio])
    mean = vectors.mean(axis=0)
    norm = np.linalg.norm(mean)
    if not np.isfinite(vectors).all() or not np.isfinite(norm) or norm < 1e-8:
        raise ValueError('Invalid reference vectors')
    mean /= norm
    directory.mkdir(parents=True)
    target = directory/'embeddings.npz'
    np.savez(target, mean_profile=mean, normalized=vectors)
    manifest = {'schema':VOICE_SCHEMA, 'platform':'chzzk', 'channel_id':channel, 'revision':revision,
                'encoder':{'id':ENCODER_ID, 'dimension':192, 'path':spec['encoder_path'], 'sha256':model_hash},
                'embeddings':{'path':'embeddings.npz', 'sha256':sha256_file(target)}}
    save_json(directory/'manifest.json', manifest)
    save_json(directory/'rights.json', {k:spec[k] for k in ('recording_rights_confirmed','model_terms_confirmed','model_source_url','model_revision','model_license')})
    validate_voice_manifest(directory/'manifest.json', root, platform='chzzk', channel_id=channel, revision=revision)
    return directory

def activate(root, channel, revision):
    identity(channel, revision)
    root = plain_path(root)
    directory = plain_path(root/'chzzk'/channel/revision)
    rights = read_json(directory/'rights.json')
    if rights.get('recording_rights_confirmed') is not True or rights.get('model_terms_confirmed') is not True:
        raise ValueError('Profile has no confirmed rights record')
    validate_voice_manifest(directory/'manifest.json', root, platform='chzzk', channel_id=channel, revision=revision)
    save_json(root/'chzzk'/channel/'active.json', {'schema':'chzz.voice-profile-selection.v1',
              'platform':'chzzk', 'channel_id':channel, 'revision':revision, 'mode':'observe'})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    item = commands.add_parser('prepare');item.add_argument('spec')
    item = commands.add_parser('activate');item.add_argument('channel');item.add_argument('revision')
    item = commands.add_parser('disable');item.add_argument('channel')
    item = commands.add_parser('observe');item.add_argument('url')
    args = parser.parse_args()
    root = plain_path(ROOT/'.voice')
    if args.command == 'prepare':
        print('Prepared, not activated:', prepare_profile(root, read_json(args.spec)))
    elif args.command == 'activate':
        activate(root,args.channel,args.revision);print('Selected this exact channel/revision for observation')
    elif args.command == 'disable':
        identity(args.channel)
        pointer = plain_path(root/'chzzk'/args.channel/'active.json')
        if pointer.exists():
            selection = read_json(pointer);selection['mode']='disabled';save_json(pointer,selection)
        print('Reference disabled; recordings and profiles retained')
    else:
        run = plain_path(ROOT/'runs'/video_number(args.url))
        metadata = read_json(run/'metadata.json')
        profile = resolve_profile('chzzk', metadata['channel_id'], selected=True,
                                  profiles_root=root/'registry', references_root=root, require_active=True)
        result = observe_reference(plain_path(run/'audio.wav'), profile)
        save_json(run/'voice-observation-private.json',result)
        print('Private observation:', result['status'], result.get('reason',''))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
