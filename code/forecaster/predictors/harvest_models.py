"""과거 입력만 사용하는 실험 모델. 서비스 기본 모델을 변경하지 않는다."""
from datetime import date, timedelta
import math

import numpy as np

FEATURE_VERSION = 'causal-ffill7-seq56-logreturn-calendar-season-v1'


def calendar_features(day):
    # 윤년과 평년에서 같은 월·일 위상이 같도록 고정 윤년을 사용한다.
    phase = (date(2000, day.month, day.day) - date(2000, 1, 1)).days / 366 * 2 * math.pi
    return [math.sin(phase), math.cos(phase)]


class History:
    def __init__(self, records, config, *, extend_through=None):
        self.config = config
        self.records = sorted(records, key=lambda r: r['date'])
        self.first, self.last = self.records[0]['date'], self.records[-1]['date']
        # 서비스 발행일이 마지막 공시일보다 늦어도 미래 관측을 만들지 않는다.
        grid_end = max(self.last, extend_through) if extend_through is not None else self.last
        self.days = (grid_end - self.first).days + 1
        self.observed = np.full(self.days, np.nan)
        for row in self.records:
            if not math.isfinite(row['price']) or row['price'] <= 0:
                raise ValueError('관측 가격은 유한한 양수여야 합니다.')
            index = (row['date'] - self.first).days
            if not np.isnan(self.observed[index]):
                raise ValueError('중복 관측 날짜입니다.')
            self.observed[index] = row['price']
        if np.any(self.observed[~np.isnan(self.observed)] <= 0) or np.isinf(self.observed).any():
            raise ValueError('관측 가격은 유한한 양수여야 합니다.')
        # 양방향 보간 대신 최대 7일 전방 채움. 미래 관측이 과거 특징에 들어갈 수 없다.
        self.filled = self.observed.copy()
        previous, lag = np.nan, 8
        for i, value in enumerate(self.observed):
            if np.isfinite(value):
                previous, lag = value, 0
            else:
                lag += 1
                if lag <= 7:
                    self.filled[i] = previous
        self._season_cache = {}
        self._feature_cache = {}
        self._training_cache = None

    def day_index(self, day):
        return (day - self.first).days

    def values(self, start, end):
        a, b = max(0, self.day_index(start)), min(self.days, self.day_index(end) + 1)
        values = self.observed[a:b] if b > a else np.array([])
        return values[np.isfinite(values)]

    def target(self, origin, lead, width, *, available_at):
        start = origin + timedelta(days=lead)
        end = start + timedelta(days=width - 1)
        if end > available_at:
            raise ValueError('target_window_not_mature')
        values = self.values(start, end)
        required = math.ceil(width * self.config['minimum_target_observed_fraction'])
        if len(values) < required:
            raise ValueError('target_not_observed' if width == 1 else 'target_window_insufficient_observations')
        return float(values.mean()), len(values), end

    def season(self, origin, target, *, min_years):
        key = origin, target, min_years
        if key in self._season_cache:
            return self._season_cache[key]
        spec = self.config['seasonal']
        samples, years = [], []
        for year in range(origin.year, origin.year - 7, -1):
            if year >= target.year:
                continue
            try:
                center = date(year, target.month, target.day)
            except ValueError:
                center = date(year, 2, 28)
            start, end = center - timedelta(days=spec['window_days']), center + timedelta(days=spec['window_days'])
            if start < self.first or end > origin:
                continue
            values = self.values(start, end)
            if len(values) < spec['min_observations_per_year']:
                continue
            samples.extend(values)
            years.append(year)
            if len(years) == spec['max_years']:
                break
        value = (float(np.median(samples)), years) if len(years) >= min_years else (None, years)
        self._season_cache[key] = value
        return value

    def features(self, origin, lead, width):
        key = origin, lead, width
        if key in self._feature_cache:
            return self._feature_cache[key]
        end = self.day_index(origin) + 1
        length = self.config['training']['sequence_days']
        if end > self.days or end < length:
            raise ValueError('sequence_too_short')
        prices = self.filled[end - length:end]
        if not np.isfinite(prices).all():
            raise ValueError('sequence_has_long_gap')
        last = float(prices[-1])
        sequence = np.array([[math.log(p / last), *calendar_features(origin - timedelta(days=length - i - 1)),
                              float(np.isfinite(self.observed[end - length + i]))]
                             for i, p in enumerate(prices)], dtype=np.float32)
        vector = [math.log(last)]
        for lag in (1, 7, 14, 28, 55, 90, 180, 365):
            index = end - 1 - lag
            value = self.filled[index] if index >= 0 else np.nan
            vector.extend([math.log(value / last) if np.isfinite(value) else 0., float(np.isfinite(value))])
        for window in (7, 28, 56):
            values = np.log(prices[-window:] / last)
            vector.extend([float(values.mean()), float(values.std())])
        center = origin + timedelta(days=lead + (width - 1) // 2)
        seasonal, _ = self.season(origin, center, min_years=1)
        vector.extend([*calendar_features(origin), *calendar_features(center), lead / 180, width / 14,
                       math.log(seasonal / last) if seasonal is not None else 0., float(seasonal is not None)])
        result = sequence, np.asarray(vector, dtype=np.float32), last
        self._feature_cache[key] = result
        return result

    def seasonal_forecasts(self, origin, lead, width):
        center = origin + timedelta(days=lead + (width - 1) // 2)
        base, years = self.season(origin, center, min_years=self.config['seasonal']['min_years'])
        if base is None:
            raise ValueError('seasonal_fewer_than_two_complete_years')
        spec = self.config['correction']
        recent = self.values(origin - timedelta(days=spec['recent_days'] - 1), origin)
        current_season, _ = self.season(origin, origin, min_years=1)
        ratio = 1.
        if current_season is not None and len(recent) >= spec['min_recent_observations']:
            ratio = float(np.clip(np.median(recent) / current_season, spec['ratio_min'], spec['ratio_max']))
        corrected = base * ratio ** math.exp(-(lead + (width - 1) / 2) / spec['decay_days'])
        return base, corrected, ratio, years

    def training_rows(self, cutoff):
        # 특징은 각 학습 기준일 이전, 정답창 말일은 모델 학습 발행일 이전이다.
        if self._training_cache is None:
            rows = []
            origin = self.first + timedelta(days=self.config['training']['sequence_days'] - 1)
            while origin < self.last:
                for lead in self.config['horizons']:
                    for width in self.config['target_windows']:
                        try:
                            target, count, end = self.target(origin, lead, width, available_at=self.last)
                            sequence, vector, last = self.features(origin, lead, width)
                        except ValueError:
                            continue
                        rows.append(dict(origin=origin, lead=lead, width=width, label_end=end, observations=count,
                                         sequence=sequence, vector=vector, y=math.log(target / last)))
                origin += timedelta(days=self.config['training']['anchor_step_days'])
            self._training_cache = rows
        # 캐시 생성 시 전체 파일을 훑더라도 현재 학습에 들어가는 입력과 정답은 이 필터로 차단한다.
        return [r for r in self._training_cache if r['label_end'] <= cutoff]


def build_lstm(feature_count, spec):
    import torch

    class SmallLSTM(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = torch.nn.LSTM(4, spec['hidden_size'], spec['layers'], batch_first=True)
            self.head = torch.nn.Sequential(torch.nn.Linear(spec['hidden_size'] + feature_count, spec['head_size']),
                                            torch.nn.ReLU(), torch.nn.Linear(spec['head_size'], 1))

        def forward(self, sequence, features):
            encoded, _ = self.encoder(sequence)
            return self.head(torch.cat([encoded[:, -1], features], dim=1)).squeeze(1)

    return SmallLSTM()


def train_and_predict(rows, queries, config, artifact_dir):
    """한 학습 발행일의 트리·LSTM 3시드. 정답창이 발행일 전에 끝나는 rows만 전달한다."""
    import time
    import joblib
    import torch
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    artifact_dir.mkdir(parents=True, exist_ok=False)
    x = np.stack([r['vector'] for r in rows])
    y = np.array([r['y'] for r in rows], dtype=np.float32)
    qx = np.stack([q[1] for q in queries])
    started = time.perf_counter()
    with threadpool_limits(limits=4):
        tree = HistGradientBoostingRegressor(**{k: v for k, v in config['tree'].items() if k != 'implementation'})
        tree.fit(x, y)
        tree_output = tree.predict(qx)
    joblib.dump(tree, artifact_dir/'tree.joblib')
    times = dict(tree_fit_predict_seconds=time.perf_counter() - started)
    scaler = StandardScaler().fit(x)
    y_mean, y_std = float(y.mean()), max(float(y.std()), .1)
    spec = config['lstm']
    if spec['device'] == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('LSTM GPU 학습을 요청했으나 CUDA를 사용할 수 없습니다.')
    device = torch.device(spec['device'])
    sequence = torch.as_tensor(np.stack([r['sequence'] for r in rows]), device=device)
    features = torch.as_tensor(scaler.transform(x).astype(np.float32), device=device)
    targets = torch.as_tensor((y - y_mean) / y_std, device=device)
    qsequence = torch.as_tensor(np.stack([q[0] for q in queries]), device=device)
    qfeatures = torch.as_tensor(scaler.transform(qx).astype(np.float32), device=device)
    predictions, seed_info = [], []
    for seed in spec['seeds']:
        started = time.perf_counter()
        torch.manual_seed(seed)
        if device.type == 'cuda':
            torch.cuda.manual_seed_all(seed)
        model = build_lstm(x.shape[1], spec).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=spec['learning_rate'], weight_decay=spec['weight_decay'])
        loss_function = torch.nn.SmoothL1Loss(beta=.5)
        rng = np.random.default_rng(seed)
        model.train()
        losses = []
        for epoch in range(spec['epochs']):
            order = rng.permutation(len(rows))
            total = 0.
            for offset in range(0, len(order), spec['batch_size']):
                ids = torch.as_tensor(order[offset:offset + spec['batch_size']], device=device)
                optimizer.zero_grad(set_to_none=True)
                prediction = model(sequence[ids], features[ids])
                loss = loss_function(prediction, targets[ids])
                if not torch.isfinite(loss):
                    raise ValueError('LSTM 학습 손실이 유효하지 않습니다.')
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), spec['gradient_clip'])
                optimizer.step()
                total += float(loss.detach()) * len(ids)
            losses.append(total / len(rows))
        model.eval()
        with torch.no_grad():
            outputs = []
            for offset in range(0, len(queries), 256):
                outputs.append(model(qsequence[offset:offset + 256], qfeatures[offset:offset + 256]).cpu().numpy())
            delta = np.concatenate(outputs) * y_std + y_mean
        if not np.isfinite(delta).all():
            raise ValueError('LSTM 출력이 유효하지 않습니다.')
        predictions.append(delta)
        torch.save(dict(state_dict={k: v.cpu() for k, v in model.state_dict().items()},
                        x_mean=scaler.mean_.tolist(), x_scale=scaler.scale_.tolist(), y_mean=y_mean, y_std=y_std,
                        feature_version=FEATURE_VERSION, config=spec, seed=seed), artifact_dir/f'lstm_seed_{seed}.pt')
        seed_info.append(dict(seed=seed, epochs=spec['epochs'], first_loss=losses[0], final_loss=losses[-1],
                              parameters=sum(p.numel() for p in model.parameters()), seconds=time.perf_counter() - started))
        del model, optimizer
    times['lstm_seed_training'] = seed_info
    return tree_output, predictions, times
