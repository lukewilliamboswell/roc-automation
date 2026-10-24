extern const long long roc_answer;
extern int lifecycle_host_matches(long long answer);

int main(void) {
    return lifecycle_host_matches(roc_answer) ? 0 : 1;
}
