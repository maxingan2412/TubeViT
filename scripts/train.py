import os
import pickle
from typing import Tuple, Optional, Callable

import click
import pytorch_lightning as pl
import torch
from torch import Tensor
from torch.utils.data import DataLoader, RandomSampler
from torchvision.datasets import UCF101
from torchvision.transforms import transforms as T
from torchvision.transforms._functional_video import resize
from torchvision.transforms._transforms_video import RandomResizedCropVideo, RandomHorizontalFlipVideo, ToTensorVideo

from TubeViT.model import TubeViTLightningModule


class ResizedVideo:
    def __init__(
        self,
        size,
        interpolation_mode="bilinear",
    ):
        if isinstance(size, tuple):
            assert len(size) == 2, "size should be tuple (height, width)"
            self.size = size
        else:
            self.size = (size, size)

        self.interpolation_mode = interpolation_mode

    def __call__(self, clip: torch.Tensor):
        """
        Args:
            clip (torch.tensor): Video clip to be cropped. Size is (C, T, H, W)
        Returns:
            torch.tensor: resized video clip.
                size is (C, T, H, W)
        """
        return resize(clip, self.size, self.interpolation_mode)

    def __repr__(self):
        return self.__class__.__name__ + \
            '(size={0}, interpolation_mode={1})'.format(
                self.size, self.interpolation_mode
            )


class MyUCF101(UCF101):
    def __init__(self, frame_transform: Optional[Callable] = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.frame_transform = frame_transform

    def __getitem__(self, idx: int) -> Tuple[Tensor, int]:
        video, audio, info, video_idx = self.video_clips.get_clip(idx)
        label = self.samples[self.indices[video_idx]][1]

        if self.transform is not None:
            video = self.transform(video)

        return video, label


@click.command()
@click.option('-r', '--dataset-root', type=click.Path(exists=True), required=True, help='path to dataset.')
@click.option('-a', '--annotation-path', type=click.Path(exists=True), required=True, help='path to dataset.')
@click.option('-nc', '--num-classes', type=int, default=101, help='num of classes of dataset.')
@click.option('-b', '--batch-size', type=int, default=32, help='batch size.')
@click.option('-f', '--frames-per-clip', type=int, default=32, help='frame per clip.')
@click.option('-v', '--video-size', type=click.Tuple([int, int]), default=(224, 224), help='frame per clip.')
@click.option('--max-epochs', type=int, default=5, help='max epochs.')
@click.option('--num-workers', type=int, default=0)
@click.option('--fast-dev-run', type=bool, is_flag=True, show_default=True, default=False)
@click.option('--seed', type=int, default=42, help='random seed.')
def main(dataset_root, annotation_path, num_classes, batch_size, frames_per_clip, video_size, max_epochs, num_workers,
         fast_dev_run, seed):
    pl.seed_everything(seed)

    train_transform = T.Compose([
        ToTensorVideo(),
        RandomHorizontalFlipVideo(),
        RandomResizedCropVideo(size=video_size),
    ])

    test_transform = T.Compose([
        ToTensorVideo(),
        ResizedVideo(size=video_size),
    ])

    train_metadata_file = 'ucf101-train-meta.pickle'
    train_precomputed_metadata = None
    if os.path.exists(train_metadata_file):
        with open(train_metadata_file, 'rb') as f:
            train_precomputed_metadata = pickle.load(f)

    train_set = MyUCF101(
        root=dataset_root,
        annotation_path=annotation_path,
        _precomputed_metadata=train_precomputed_metadata,
        frames_per_clip=frames_per_clip,
        train=True,
        output_format='THWC',
        num_workers=num_workers,
        transform=train_transform,
    )

    if not os.path.exists(train_metadata_file):
        with open(train_metadata_file, 'wb') as f:
            pickle.dump(train_set.metadata, f, protocol=pickle.HIGHEST_PROTOCOL)

    val_metadata_file = 'ucf101-val-meta.pickle'
    val_precomputed_metadata = None
    if os.path.exists(val_metadata_file):
        with open(val_metadata_file, 'rb') as f:
            val_precomputed_metadata = pickle.load(f)

    val_set = MyUCF101(
        root=dataset_root,
        annotation_path=annotation_path,
        _precomputed_metadata=val_precomputed_metadata,
        frames_per_clip=frames_per_clip,
        train=False,
        output_format='THWC',
        num_workers=num_workers,
        transform=test_transform,
    )

    if not os.path.exists(val_metadata_file):
        with open(val_metadata_file, 'wb') as f:
            pickle.dump(val_set.metadata, f, protocol=pickle.HIGHEST_PROTOCOL)

    train_sampler = RandomSampler(train_set, num_samples=len(train_set) // 10)
    train_dataloader = DataLoader(train_set,
                                  batch_size=batch_size,
                                  num_workers=num_workers,
                                  shuffle=False,
                                  drop_last=True,
                                  sampler=train_sampler)

    val_sampler = RandomSampler(val_set, num_samples=len(val_set) // 10)
    val_dataloader = DataLoader(val_set,
                                batch_size=batch_size,
                                num_workers=num_workers,
                                shuffle=False,
                                drop_last=True,
                                sampler=val_sampler)

    x, y = next(iter(train_dataloader))
    print(x.shape)

    model = TubeViTLightningModule(num_classes=num_classes,
                                   video_shape=x.shape[1:],
                                   num_layers=4,
                                   num_heads=12,
                                   hidden_dim=768,
                                   mlp_dim=3072,
                                   lr=1e-4,
                                   weight_path='tubevit_b_(a+iv)+(d+v)+(e+iv)+(f+v).pt')

    trainer = pl.Trainer(max_epochs=max_epochs, accelerator='auto', fast_dev_run=fast_dev_run)
    trainer.fit(model, train_dataloaders=train_dataloader, val_dataloaders=val_dataloader)
    trainer.save_checkpoint('./models/tubevit_ucf101.ckpt')


if __name__ == '__main__':
    main()