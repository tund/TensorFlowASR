# Copyright 2020 Huy Le Nguyen (@usimarit)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import argparse
from tensorflow_asr.utils import setup_environment, setup_strategy

setup_environment()
import tensorflow as tf

# example: python train_streaming_transducer.py --config config.yml --max_ckpts 10 --tfrecords True --tbs 512 --ebs 256 

DEFAULT_YAML = os.path.join(os.path.abspath(os.path.dirname(__file__)), "config.yml")

tf.keras.backend.clear_session()

parser = argparse.ArgumentParser(prog="Conformer Training")

parser.add_argument("--config", type=str, default=DEFAULT_YAML,
                    help="The file path of model configuration file")

parser.add_argument("--max_ckpts", type=int, default=10,
                    help="Max number of checkpoints to keep")

parser.add_argument("--tfrecords", default=False, action="store_true",
                    help="Whether to use tfrecords")

parser.add_argument("--tbs", type=int, default=None,
                    help="Train batch size per replica")

parser.add_argument("--ebs", type=int, default=None,
                    help="Evaluation batch size per replica")

parser.add_argument("--devices", type=int, nargs="*", default=[0],
                    help="Devices' ids to apply distributed training")

parser.add_argument("--mxp", default=False, action="store_true",
                    help="Enable mixed precision")

parser.add_argument("--cache", default=False, action="store_true",
                    help="Enable caching for dataset")

args = parser.parse_args()

tf.config.optimizer.set_experimental_options({"auto_mixed_precision": args.mxp})

strategy = setup_strategy(args.devices)

from tensorflow_asr.configs.config import Config
from tensorflow_asr.datasets.asr_dataset import ASRTFRecordDataset, ASRSliceDataset
from tensorflow_asr.featurizers.speech_featurizers import TFSpeechFeaturizer
from tensorflow_asr.featurizers.text_featurizers import CharFeaturizer
from tensorflow_asr.runners.transducer_runners import TransducerTrainer
from tensorflow_asr.models.streaming_transducer import StreamingTransducer

config = Config(args.config, learning=True)
speech_featurizer = TFSpeechFeaturizer(config.speech_config)
text_featurizer = CharFeaturizer(config.decoder_config)

if args.tfrecords:
    train_dataset = ASRTFRecordDataset(
        data_paths=config.learning_config.dataset_config.train_paths,
        tfrecords_dir=config.learning_config.dataset_config.tfrecords_dir,
        speech_featurizer=speech_featurizer,
        text_featurizer=text_featurizer,
        augmentations=config.learning_config.augmentations,
        stage="train", cache=args.cache, shuffle=True
    )
    eval_dataset = ASRTFRecordDataset(
        data_paths=config.learning_config.dataset_config.eval_paths,
        tfrecords_dir=config.learning_config.dataset_config.tfrecords_dir,
        speech_featurizer=speech_featurizer,
        text_featurizer=text_featurizer,
        stage="eval", cache=args.cache, shuffle=True
    )
else:
    train_dataset = ASRSliceDataset(
        data_paths=config.learning_config.dataset_config.train_paths,
        speech_featurizer=speech_featurizer,
        text_featurizer=text_featurizer,
        augmentations=config.learning_config.augmentations,
        stage="train", cache=args.cache, shuffle=True
    )
    eval_dataset = ASRSliceDataset(
        data_paths=config.learning_config.dataset_config.eval_paths,
        speech_featurizer=speech_featurizer,
        text_featurizer=text_featurizer,
        stage="eval", cache=args.cache, shuffle=True
    )

streaming_transducer_trainer = TransducerTrainer(
    config=config.learning_config.running_config,
    text_featurizer=text_featurizer, strategy=strategy
)

with streaming_transducer_trainer.strategy.scope():
    # build model
    streaming_transducer = StreamingTransducer(
        **config.model_config,
        vocabulary_size=text_featurizer.num_classes
    )
    streaming_transducer._build(speech_featurizer.shape)
    streaming_transducer.summary(line_length=150)

    optimizer = tf.keras.optimizers.get(config.learning_config.optimizer_config)

streaming_transducer_trainer.compile(model=streaming_transducer, optimizer=optimizer,
                                     max_to_keep=args.max_ckpts)

streaming_transducer_trainer.fit(train_dataset, eval_dataset, train_bs=args.tbs, eval_bs=args.ebs)
